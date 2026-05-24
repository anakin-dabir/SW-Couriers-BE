"""Integration tests for credit account cool-down settings (global + org cascade)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.modules.org_credit.enums import OrgCreditAccountStatus
from app.modules.org_credit.models import OrgCreditAccount, OrgCreditLedgerEntry
from app.modules.org_credit_settings.models import OrgCreditCooldownWindow
from app.modules.user.models import User
from app.modules.organizations.models import Organization


@pytest.mark.asyncio
async def test_global_cooldown_get_patch_and_org_cascade(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    db_session.add(
        OrgCreditAccount(
            organization_id=org.id,
            status=OrgCreditAccountStatus.ACTIVE,
            used_credit=Decimal("0"),
        ),
    )
    await db_session.flush()

    r0 = await client.get("/v1/organizations/credit/settings/cooldown-period", headers=admin_headers)
    assert r0.status_code == 200
    body0 = r0.json()["data"]
    assert set(body0.keys()) == {"months", "days", "hours"}
    assert all(isinstance(body0[k], int) for k in ("months", "days", "hours"))

    r_patch = await client.patch(
        "/v1/organizations/credit/settings/cooldown-period",
        headers=admin_headers,
        json={"months": 6, "days": 0, "hours": 0, "reset_to_defaults": False},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["data"] == {"months": 6, "days": 0, "hours": 0}

    r_org = await client.get(
        f"/v1/organizations/{org.id}/credit/settings/cooldown-period",
        headers=admin_headers,
    )
    assert r_org.status_code == 200
    assert r_org.json()["data"] == {"months": 6, "days": 0, "hours": 0}

    r_post = await client.post(
        f"/v1/organizations/{org.id}/credit/settings/cooldown-period",
        headers=admin_headers,
        json={"months": 1, "days": 0, "hours": 0, "reset_to_defaults": False},
    )
    assert r_post.status_code == 200
    assert r_post.json()["data"] == {"months": 1, "days": 0, "hours": 0}

    ledger_total_stmt = select(func.count()).select_from(OrgCreditLedgerEntry).where(
        OrgCreditLedgerEntry.organization_id == org.id,
    )
    n = (await db_session.execute(ledger_total_stmt)).scalar_one()
    assert n == 0

    r_sett = await client.get(
        f"/v1/organizations/{org.id}/credit/settings",
        headers=admin_headers,
    )
    assert r_sett.status_code == 200
    assert r_sett.json()["data"]["cooldown_section"] == {"months": 1, "days": 0, "hours": 0}

    r_clear = await client.post(
        f"/v1/organizations/{org.id}/credit/settings/cooldown-period",
        headers=admin_headers,
        json={"reset_to_defaults": True},
    )
    assert r_clear.status_code == 200
    assert r_clear.json()["data"] == {"months": 6, "days": 0, "hours": 0}


@pytest.mark.asyncio
async def test_cooldown_reset_with_triplet_returns_422(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    bad = await client.post(
        f"/v1/organizations/{org.id}/credit/settings/cooldown-period",
        headers=admin_headers,
        json={"months": 0, "days": 0, "hours": 0, "reset_to_defaults": True},
    )
    assert bad.status_code == 422
    bad_global = await client.patch(
        "/v1/organizations/credit/settings/cooldown-period",
        headers=admin_headers,
        json={"months": 1, "days": 0, "hours": 0, "reset_to_defaults": True},
    )
    assert bad_global.status_code == 422


@pytest.mark.asyncio
async def test_org_active_cooldown_get(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    r = await client.get(
        f"/v1/organizations/{org.id}/credit/settings/active-cooldown",
        headers=admin_headers,
    )
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["active"] is False
    assert d["ends_at"] is None
    assert d["remaining_seconds"] is None
    assert d["summary"] is None

    now = datetime.now(UTC)
    end = now + timedelta(days=7)
    db_session.add(
        OrgCreditCooldownWindow(
            organization_id=org.id,
            started_at=now,
            ends_at=end,
            policy_months=0,
            policy_days=7,
            policy_hours=0,
        ),
    )
    await db_session.flush()

    r2 = await client.get(
        f"/v1/organizations/{org.id}/credit/settings/active-cooldown",
        headers=admin_headers,
    )
    assert r2.status_code == 200
    d2 = r2.json()["data"]
    assert d2["active"] is True
    assert d2["ends_at"] is not None
    assert d2["remaining_seconds"] is not None and d2["remaining_seconds"] > 0
    assert d2["summary"] is not None


@pytest.mark.asyncio
async def test_patch_payment_terms_via_settings_router(
    client: AsyncClient,
    admin_headers: dict[str, str],
    admin_user: User,
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    db_session.add(
        OrgCreditAccount(
            organization_id=org.id,
            status=OrgCreditAccountStatus.ACTIVE,
            used_credit=Decimal("0"),
        ),
    )
    await db_session.flush()

    eff = datetime.now(UTC).date() - timedelta(days=1)
    r = await client.patch(
        f"/v1/organizations/{org.id}/credit/settings/payment-terms",
        headers=admin_headers,
        json={
            "payment_terms_days": 30,
            "effective_date": eff.isoformat(),
            "reason": "Commercial agreement",
            "apply_to_existing_unpaid": False,
        },
    )
    assert r.status_code == 200
    assert r.json()["message"] == "Payment terms updated."

    r2 = await client.get(
        f"/v1/organizations/{org.id}/credit/settings",
        headers=admin_headers,
    )
    assert r2.status_code == 200
    cts = r2.json()["data"]["credit_terms_section"]
    assert cts["payment_terms_days"] == 30
    assert cts["last_updated"] is not None

    hist = await client.get(
        f"/v1/organizations/{org.id}/credit/settings/terms-history",
        headers=admin_headers,
        params={"page": 1, "size": 20},
    )
    assert hist.status_code == 200
    payload = hist.json()["data"]
    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    entry = payload["items"][0]
    assert entry["new_terms"] == "30"
    assert entry["old_terms"] is None
    assert entry["effective_date"] == eff.isoformat()
    assert entry["reason"] == "Commercial agreement"
    assert entry["applied_to_existing"] is False
    assert entry["modified_by"] is not None
    assert entry["modified_by"]["id"] == admin_user.id
    assert entry["modified_by"]["first_name"] == admin_user.first_name
    assert entry["modified_by"]["last_name"] == admin_user.last_name

    eff2 = datetime.now(UTC).date()
    r3 = await client.patch(
        f"/v1/organizations/{org.id}/credit/settings/payment-terms",
        headers=admin_headers,
        json={
            "payment_terms_days": 14,
            "effective_date": eff2.isoformat(),
            "reason": "Second change",
            "apply_to_existing_unpaid": True,
        },
    )
    assert r3.status_code == 200

    hist2 = await client.get(
        f"/v1/organizations/{org.id}/credit/settings/terms-history",
        headers=admin_headers,
        params={"page": 1, "size": 20},
    )
    assert hist2.status_code == 200
    data2 = hist2.json()["data"]
    assert data2["total"] == 2
    assert data2["items"][0]["new_terms"] == "14"
    assert data2["items"][0]["old_terms"] == "30"
    assert data2["items"][1]["new_terms"] == "30"


@pytest.mark.asyncio
async def test_risk_controls_get_patch_does_not_write_hold_threshold_ledger(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    db_session.add(
        OrgCreditAccount(
            organization_id=org.id,
            status=OrgCreditAccountStatus.ACTIVE,
            used_credit=Decimal("0"),
            hold_threshold_pct=75,
        ),
    )
    await db_session.flush()

    stmt = select(func.count()).select_from(OrgCreditLedgerEntry).where(
        OrgCreditLedgerEntry.organization_id == org.id,
    )
    n_before = (await db_session.execute(stmt)).scalar_one()

    r_get = await client.get(
        f"/v1/organizations/{org.id}/credit/settings/risk-controls",
        headers=admin_headers,
    )
    assert r_get.status_code == 200
    assert r_get.json()["data"]["hold_threshold_pct"] == 75

    r_patch = await client.patch(
        f"/v1/organizations/{org.id}/credit/settings/risk-controls",
        headers=admin_headers,
        json={"hold_threshold_pct": 85},
    )
    assert r_patch.status_code == 200

    r_get2 = await client.get(
        f"/v1/organizations/{org.id}/credit/settings/risk-controls",
        headers=admin_headers,
    )
    assert r_get2.status_code == 200
    assert r_get2.json()["data"]["hold_threshold_pct"] == 85

    n_after = (await db_session.execute(stmt)).scalar_one()
    assert n_after == n_before


@pytest.mark.asyncio
async def test_get_risk_controls_returns_404_without_credit_account(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.get(
        f"/v1/organizations/{org.id}/credit/settings/risk-controls",
        headers=admin_headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_risk_controls_returns_422_without_credit_account(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.patch(
        f"/v1/organizations/{org.id}/credit/settings/risk-controls",
        headers=admin_headers,
        json={"hold_threshold_pct": 80},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_payment_terms_returns_422_without_credit_account(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.patch(
        f"/v1/organizations/{org.id}/credit/settings/payment-terms",
        headers=admin_headers,
        json={
            "payment_terms_days": 30,
            "effective_date": date(2026, 6, 1).isoformat(),
            "reason": "Test",
            "apply_to_existing_unpaid": False,
        },
    )
    assert r.status_code == 422
