from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.modules.org_credit.enums import OrgCreditAccountStatus
from app.modules.org_credit.models import OrgCreditAccount
from app.modules.org_credit_alerts.enums import (
    CreditAlertSeverity,
    CreditAlertStatus,
    CreditAlertType,
)
from app.modules.org_credit_alerts.models import OrgCreditAlert
from app.modules.organizations.models import Organization


async def _create_credit_account(db_session, org_id: str) -> OrgCreditAccount:
    acct = OrgCreditAccount(
        organization_id=org_id,
        status=OrgCreditAccountStatus.ACTIVE,
        credit_limit=Decimal("10000.00"),
        used_credit=Decimal("1000.00"),
        payment_terms_days=30,
    )
    db_session.add(acct)
    await db_session.flush()
    return acct


@pytest.mark.asyncio
async def test_credit_overview_no_account_null_placeholders(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.get(
        f"/v1/organizations/{org.id}/credit/overview",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["account"] is None
    assert d["credit_status"] is None
    ob = d["outstanding_balance"]
    assert ob["total"] is None
    assert ob["current"] is None
    assert d["next_invoice"]["due_date"] is None
    assert "NO_CREDIT_ACCOUNT" in d["risk_flags"]


@pytest.mark.asyncio
async def test_credit_overview_with_account_nested_shape(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    await _create_credit_account(db_session, org.id)
    r = await client.get(
        f"/v1/organizations/{org.id}/credit/overview",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["account"] is not None
    assert d["credit_status"]["status"] == "ACTIVE"
    assert d["credit_limit"]["amount"] == "10000.00"
    assert d["credit_terms"]["terms_label"] == "Net 30"
    assert d["outstanding_balance"]["total"] == "1000.00"
    assert d["overdue"]["total"] is None
    ics = d["internal_credit_score"]
    assert ics is None or ics.get("score") is None or isinstance(ics.get("score"), int)


@pytest.mark.asyncio
async def test_overview_limit_trend_returns_list(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.get(
        f"/v1/organizations/{org.id}/credit/overview/limit-trend",
        headers=admin_headers,
        params={"year": 2026, "granularity": "monthly"},
    )
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["data"], list)


@pytest.mark.asyncio
async def test_overview_utilisation_trend_daily_requires_month(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.get(
        f"/v1/organizations/{org.id}/credit/overview/utilisation-trend",
        headers=admin_headers,
        params={"year": 2026, "granularity": "daily"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_overview_utilisation_trend_daily_with_month_ok(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.get(
        f"/v1/organizations/{org.id}/credit/overview/utilisation-trend",
        headers=admin_headers,
        params={"year": 2026, "month": 3, "granularity": "daily"},
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_overview_active_alerts_capped_at_three_most_recent(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    await _create_credit_account(db_session, org.id)
    base = datetime.now(UTC)
    types_ = [
        CreditAlertType.CREDIT_UTILISATION_MONITORING_WARNING,
        CreditAlertType.CREDIT_UTILISATION_MONITORING_CRITICAL,
        CreditAlertType.REVIEW_OVERDUE,
        CreditAlertType.CREDIT_SCORE_DECREASE,
    ]
    for i, at in enumerate(types_):
        db_session.add(
            OrgCreditAlert(
                organization_id=org.id,
                alert_type=at,
                severity=CreditAlertSeverity.WARNING,
                status=CreditAlertStatus.ACTIVE,
                title=f"T{i}",
                summary=f"S{i}",
                triggered_at=base - timedelta(minutes=i),
            ),
        )
    await db_session.flush()

    r = await client.get(
        f"/v1/organizations/{org.id}/credit/overview/active-alerts",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert len(rows) == 3
    assert rows[0]["title"] == "T0"
    assert rows[1]["title"] == "T1"
    assert rows[2]["title"] == "T2"
