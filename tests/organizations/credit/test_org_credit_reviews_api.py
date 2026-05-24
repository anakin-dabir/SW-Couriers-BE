"""API tests for organisation credit review routes under `/v1/organizations/{org_id}/credit/reviews`."""

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.modules.org_credit.enums import OrgCreditAccountStatus, OrgCreditReviewFrequency
from app.modules.org_credit.models import OrgCreditAccount, OrgCreditReport
from app.modules.org_credit_reviews.models import OrgCreditReview
from app.modules.organizations.models import Organization

async def _create_credit_account(
    db_session,
    org: Organization,
    *,
    review_frequency: OrgCreditReviewFrequency | None = OrgCreditReviewFrequency.QUARTERLY,
) -> OrgCreditAccount:
    acct = OrgCreditAccount(
        organization_id=org.id,
        status=OrgCreditAccountStatus.ACTIVE,
        credit_limit=Decimal("10000.00"),
        used_credit=Decimal("0"),
        review_frequency=review_frequency,
    )
    db_session.add(acct)
    await db_session.flush()
    await db_session.refresh(acct)
    return acct


@pytest.mark.asyncio
async def test_get_reviews_summary_snapshot_null_without_credit_account(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.get(
        f"/v1/organizations/{org.id}/credit/reviews/summary",
        headers=admin_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["snapshot"] is None


@pytest.mark.asyncio
async def test_get_reviews_summary_returns_snapshot_when_account_exists(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    await _create_credit_account(db_session, org)
    r = await client.get(
        f"/v1/organizations/{org.id}/credit/reviews/summary",
        headers=admin_headers,
    )
    assert r.status_code == 200
    snap = r.json()["data"]["snapshot"]
    assert snap is not None
    assert "status" in snap
    assert "credit_limit" in snap
    assert "last_review_date" in snap
    assert "next_review_due" in snap
    assert "risk_level" in snap


@pytest.mark.asyncio
async def test_list_reviews_history_empty(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.get(
        f"/v1/organizations/{org.id}/credit/reviews-history",
        headers=admin_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_post_review_201_and_list_history(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    await _create_credit_account(db_session, org)

    payload = {
        "risk_level": "LOW",
        "outcome": "MAINTAIN_CURRENT_TERMS",
        "review_notes": "OK",
    }
    r = await client.post(
        f"/v1/organizations/{org.id}/credit/reviews",
        headers=admin_headers,
        json=payload,
    )
    assert r.status_code == 201
    assert r.json()["message"] == "Credit review submitted."

    r2 = await client.get(
        f"/v1/organizations/{org.id}/credit/reviews-history",
        headers=admin_headers,
    )
    assert r2.status_code == 200
    hist = r2.json()["data"]
    assert hist["total"] == 1
    assert hist["items"][0]["risk_level"] == "LOW"
    assert hist["items"][0]["outcome"] == "MAINTAIN_CURRENT_TERMS"


@pytest.mark.asyncio
async def test_post_review_422_without_credit_account(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.post(
        f"/v1/organizations/{org.id}/credit/reviews",
        headers=admin_headers,
        json={
            "risk_level": "LOW",
            "outcome": "MAINTAIN_CURRENT_TERMS",
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_review_422_increase_limit_without_recommended_limit(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    await _create_credit_account(db_session, org)
    r = await client.post(
        f"/v1/organizations/{org.id}/credit/reviews",
        headers=admin_headers,
        json={
            "risk_level": "LOW",
            "outcome": "INCREASE_LIMIT",
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_review_forbidden_for_non_admin(
    client: AsyncClient,
    auth_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    await _create_credit_account(db_session, org)
    r = await client.post(
        f"/v1/organizations/{org.id}/credit/reviews",
        headers=auth_headers,
        json={
            "risk_level": "LOW",
            "outcome": "MAINTAIN_CURRENT_TERMS",
        },
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_review_detail_404_unknown_review(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    rid = str(uuid.uuid4())
    r = await client.get(
        f"/v1/organizations/{org.id}/credit/reviews/{rid}",
        headers=admin_headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_review_detail_includes_creditsafe_when_report_exists(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    await _create_credit_account(db_session, org)
    report = OrgCreditReport(organization_id=org.id, connect_id="GB-TEST-1", credit_score=50)
    db_session.add(report)
    await db_session.flush()
    await db_session.refresh(report)

    r_post = await client.post(
        f"/v1/organizations/{org.id}/credit/reviews",
        headers=admin_headers,
        json={
            "risk_level": "MEDIUM",
            "outcome": "MAINTAIN_CURRENT_TERMS",
            "credit_report_id": report.id,
        },
    )
    assert r_post.status_code == 201

    stmt = select(OrgCreditReview.id).where(OrgCreditReview.organization_id == org.id).limit(1)
    review_id = (await db_session.execute(stmt)).scalar_one()

    r_get = await client.get(
        f"/v1/organizations/{org.id}/credit/reviews/{review_id}",
        headers=admin_headers,
    )
    assert r_get.status_code == 200
    detail = r_get.json()["data"]
    assert detail["id"] == review_id
    assert detail["creditsafe"] is not None
    assert detail["creditsafe"]["id"] == report.id
    assert detail["creditsafe"]["connect_id"] == "GB-TEST-1"


@pytest.mark.asyncio
async def test_post_review_422_invalid_credit_report_id(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    await _create_credit_account(db_session, org)
    r = await client.post(
        f"/v1/organizations/{org.id}/credit/reviews",
        headers=admin_headers,
        json={
            "risk_level": "LOW",
            "outcome": "MAINTAIN_CURRENT_TERMS",
            "credit_report_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 422
