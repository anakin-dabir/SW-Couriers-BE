"""HTTP API tests for org credit limit increase requests."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.integrations.creditsafe.client import _STUB_REPORT
from app.modules.organizations.enums import ContactRole, ContactStatus
from app.modules.organizations.models import OrgContact, Organization
from app.modules.user.models import User

from tests.organizations.credit.test_org_credit_applications_api import _applications_base, _valid_create_payload

ORGS = "/v1/organizations"


def _limit_increase_base(org_id: str) -> str:
    return f"{ORGS}/{org_id}/credit/limit-increase-requests"


async def _b2b_account_owner(db_session, user_factory, org: Organization) -> User:
    user = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    contact = OrgContact(
        organization_id=org.id,
        user_id=user.id,
        contact_number=f"+447700{uuid.uuid4().int % 1000000:06d}",
        contact_role=ContactRole.ACCOUNT_OWNER.value,
        status=ContactStatus.ACTIVE.value,
        is_primary=True,
    )
    db_session.add(contact)
    await db_session.flush()
    return user


def _b2b_headers(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    token, _ = create_access_token(
        user_id=user.id,
        role=user.role,
        client_type="CUSTOMER_B2B",
        region_id=None,
        organization_id=user.organization_id,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "CUSTOMER_B2B",
    }


async def _create_credit_account(db_session, org_id: str, *, credit_limit: Decimal = Decimal("10000.00")):
    from app.modules.org_credit.enums import OrgCreditAccountStatus
    from app.modules.org_credit.models import OrgCreditAccount

    acct = OrgCreditAccount(
        organization_id=org_id,
        status=OrgCreditAccountStatus.ACTIVE,
        credit_limit=credit_limit,
        used_credit=Decimal("0"),
    )
    db_session.add(acct)
    await db_session.flush()
    return acct


@pytest.fixture
def creditsafe_run_stub():
    with patch(
        "app.modules.org_credit_applications.service.run_credit_assessment",
        new_callable=AsyncMock,
        return_value=("stub-connect-id", dict(_STUB_REPORT)),
    ) as m:
        yield m


@pytest.mark.asyncio
async def test_create_list_approve_limit_increase_request(
    client: AsyncClient,
    admin_headers: dict,
    admin_user: User,
    db_session,
    user_factory,
    org_factory,
) -> None:
    org = await org_factory()
    await _create_credit_account(db_session, org.id, credit_limit=Decimal("25000.00"))
    b2b = await _b2b_account_owner(db_session, user_factory, org)
    b2b_h = _b2b_headers(b2b)
    base = _limit_increase_base(org.id)

    create = await client.post(
        base,
        headers=b2b_h,
        json={
            "requested_credit_limit": "40000.00",
            "reason": "Increased shipment volume.",
        },
    )
    assert create.status_code == 201, create.text
    rid = create.json()["data"]["id"]
    assert create.json()["data"]["status"] == "PENDING"
    assert create.json()["data"]["requested_by"]["id"] == b2b.id

    dup = await client.post(
        base,
        headers=b2b_h,
        json={"requested_credit_limit": "50000.00", "reason": "Another."},
    )
    assert dup.status_code == 422

    lst = await client.get(base, headers=b2b_h)
    assert lst.status_code == 200
    assert lst.json()["data"]["total"] >= 1

    one = await client.get(f"{base}/{rid}", headers=b2b_h)
    assert one.status_code == 200
    assert one.json()["data"]["id"] == rid

    appr = await client.post(
        f"{base}/{rid}/approve",
        headers=admin_headers,
        json={"approved_credit_limit": "35000.00"},
    )
    assert appr.status_code == 200, appr.text
    assert appr.json()["data"]["status"] == "APPROVED"
    assert appr.json()["data"]["approved_limit"] == "35000.00"
    assert appr.json()["data"]["reviewed_by"]["id"] == admin_user.id

    settings = await client.get(f"{ORGS}/{org.id}/credit/settings", headers=admin_headers)
    assert settings.status_code == 200
    assert settings.json()["data"]["credit_limit_section"]["total_limit"] == "35000.00"


@pytest.mark.asyncio
async def test_reject_limit_increase_request(
    client: AsyncClient,
    admin_headers: dict,
    admin_user: User,
    db_session,
    user_factory,
    org_factory,
) -> None:
    org = await org_factory()
    await _create_credit_account(db_session, org.id)
    b2b = await _b2b_account_owner(db_session, user_factory, org)
    b2b_h = _b2b_headers(b2b)
    base = _limit_increase_base(org.id)

    create = await client.post(
        base,
        headers=b2b_h,
        json={"requested_credit_limit": "30000.00", "reason": "Growth."},
    )
    assert create.status_code == 201
    rid = create.json()["data"]["id"]

    rej = await client.post(f"{base}/{rid}/reject", headers=admin_headers)
    assert rej.status_code == 200, rej.text
    assert rej.json()["data"]["status"] == "REJECTED"
    assert rej.json()["data"]["reviewed_by"]["id"] == admin_user.id
    assert rej.json()["data"]["approved_limit"] is None


@pytest.mark.asyncio
async def test_current_application_includes_pending_increase_when_approved(
    client: AsyncClient,
    admin_headers: dict,
    admin_user: User,
    db_session,
    user_factory,
    sample_org: Organization,
    creditsafe_run_stub: AsyncMock,
) -> None:
    org_id = sample_org.id
    await _create_credit_account(db_session, org_id, credit_limit=Decimal("25000.00"))
    b2b = await _b2b_account_owner(db_session, user_factory, sample_org)
    b2b_h = _b2b_headers(b2b)

    app_id = await client.post(
        _applications_base(org_id),
        headers=admin_headers,
        files={
            "application_data": (
                None,
                json.dumps(_valid_create_payload()),
                "application/json",
            ),
        },
    )
    assert app_id.status_code == 201
    app_id = app_id.json()["data"]["id"]

    await client.post(
        f"{_applications_base(org_id)}/{app_id}/assign-reviewer",
        headers=admin_headers,
        json={"reviewer_user_id": admin_user.id},
    )
    detail = await client.get(f"{_applications_base(org_id)}/{app_id}", headers=admin_headers)
    for ref in detail.json()["data"]["trade_references"]:
        await client.patch(
            f"{_applications_base(org_id)}/{app_id}/trade-references/{ref['id']}/verify",
            headers=admin_headers,
            json={"verification_status": "VERIFIED"},
        )
    await client.post(f"{_applications_base(org_id)}/{app_id}/credit-check/run", headers=admin_headers)
    await client.post(f"{_applications_base(org_id)}/{app_id}/ready-for-decision", headers=admin_headers)
    await client.post(
        f"{_applications_base(org_id)}/{app_id}/approve",
        headers=admin_headers,
        json={
            "approved_credit_limit": "25000.00",
            "approved_payment_terms_days": 30,
            "review_frequency": "QUARTERLY",
            "approval_notes": "Ok.",
        },
    )

    inc = await client.post(
        _limit_increase_base(org_id),
        headers=b2b_h,
        json={"requested_credit_limit": "40000.00", "reason": "More volume."},
    )
    assert inc.status_code == 201

    cur = await client.get(
        f"{_applications_base(org_id)}/current-application",
        headers=admin_headers,
    )
    assert cur.status_code == 200
    data = cur.json()["data"]
    assert data["status"] == "APPROVED"
    assert data["pending_credit_limit_increase_request"] is not None
    assert data["pending_credit_limit_increase_request"]["status"] == "PENDING"
    assert data["pending_credit_limit_increase_request"]["requested_limit"] == "40000.00"
