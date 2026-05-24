from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.modules.org_credit.enums import OrgCreditAccountStatus
from app.modules.org_credit_settings.enums import CreditLimitAdjustmentReason
from app.modules.org_credit.models import OrgCreditAccount
from app.modules.org_credit_settings.enums import ScheduledCreditSettingStatus
from app.modules.org_credit_settings.models import OrgCreditLimitAdjustmentHistory
from app.modules.org_credit_settings.service import OrgCreditSettingsService
from app.modules.organizations.models import Organization
from app.modules.user.models import User


@pytest.mark.asyncio
async def test_patch_credit_limit_immediate_writes_applied_history(
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
            credit_limit=Decimal("10000.00"),
            used_credit=Decimal("0"),
        ),
    )
    await db_session.flush()

    eff = datetime.now(UTC).date() - timedelta(days=1)
    r = await client.patch(
        f"/v1/organizations/{org.id}/credit/settings/adjust-limit",
        headers=admin_headers,
        json={
            "credit_limit": "25000.00",
            "reason_category": CreditLimitAdjustmentReason.BUSINESS_GROWTH.value,
            "effective_date": eff.isoformat(),
            "justification": "Growth",
        },
    )
    assert r.status_code == 200
    assert r.json()["message"] == "Credit limit updated."

    gs = await client.get(
        f"/v1/organizations/{org.id}/credit/settings",
        headers=admin_headers,
    )
    assert gs.status_code == 200
    cls = gs.json()["data"]["credit_limit_section"]
    assert cls["total_limit"] == "25000.00"

    q = await db_session.execute(select(OrgCreditLimitAdjustmentHistory).where(
        OrgCreditLimitAdjustmentHistory.organization_id == org.id,
    ))
    row = q.scalar_one()
    assert row.status == ScheduledCreditSettingStatus.APPLIED
    assert row.applied_at is not None


@pytest.mark.asyncio
async def test_patch_credit_limit_future_schedules_pending_and_history(
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
            credit_limit=Decimal("10000.00"),
            used_credit=Decimal("0"),
        ),
    )
    await db_session.flush()

    eff = datetime.now(UTC).date() + timedelta(days=10)
    r = await client.patch(
        f"/v1/organizations/{org.id}/credit/settings/adjust-limit",
        headers=admin_headers,
        json={
            "credit_limit": "40000.00",
            "reason_category": CreditLimitAdjustmentReason.BUSINESS_GROWTH.value,
            "effective_date": eff.isoformat(),
            "justification": "Future",
        },
    )
    assert r.status_code == 200
    assert r.json()["message"] == "Credit limit updated."

    gs = await client.get(
        f"/v1/organizations/{org.id}/credit/settings",
        headers=admin_headers,
    )
    assert gs.status_code == 200
    cls = gs.json()["data"]["credit_limit_section"]
    assert cls["total_limit"] == "10000.00"

    q_acct = await db_session.execute(
        select(OrgCreditAccount).where(OrgCreditAccount.organization_id == org.id),
    )
    acct_row = q_acct.scalar_one()
    assert acct_row.pending_credit_limit == Decimal("40000.00")

    q = await db_session.execute(select(OrgCreditLimitAdjustmentHistory).where(
        OrgCreditLimitAdjustmentHistory.organization_id == org.id,
    ))
    row = q.scalar_one()
    assert row.status == ScheduledCreditSettingStatus.SCHEDULED
    assert row.applied_at is None


@pytest.mark.asyncio
async def test_apply_due_scheduled_promotes_limit(
    db_session,
    org_factory,
) -> None:
    org: Organization = await org_factory()
    acct = OrgCreditAccount(
        organization_id=org.id,
        status=OrgCreditAccountStatus.ACTIVE,
        credit_limit=Decimal("10000.00"),
        used_credit=Decimal("0"),
        pending_credit_limit=Decimal("50000.00"),
        pending_credit_limit_effective_from=datetime.now(UTC).date() - timedelta(days=1),
    )
    db_session.add(acct)
    await db_session.flush()

    svc = OrgCreditSettingsService(db_session, request=None)
    n = await svc.apply_due_scheduled_credit_and_terms(datetime.now(UTC).date())
    assert n >= 1

    await db_session.refresh(acct)
    assert acct.credit_limit == Decimal("50000.00")
    assert acct.pending_credit_limit is None


@pytest.mark.asyncio
async def test_limit_history_list_uses_adjustment_table_only(
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
            credit_limit=Decimal("10000.00"),
            used_credit=Decimal("0"),
        ),
    )
    await db_session.flush()

    eff = datetime.now(UTC).date() - timedelta(days=1)
    await client.patch(
        f"/v1/organizations/{org.id}/credit/settings/adjust-limit",
        headers=admin_headers,
        json={
            "credit_limit": "20000.00",
            "reason_category": CreditLimitAdjustmentReason.BUSINESS_GROWTH.value,
            "effective_date": eff.isoformat(),
            "justification": "Test",
        },
    )

    r = await client.get(
        f"/v1/organizations/{org.id}/credit/settings/limit-history",
        headers=admin_headers,
        params={"page": 1, "size": 20},
    )
    assert r.status_code == 200
    payload = r.json()["data"]
    assert payload["total"] == 1
    assert payload["items"][0]["new_limit"] == "20000.00"
    assert payload["items"][0]["status"] == "APPLIED"
    ub = payload["items"][0]["updated_by"]
    assert ub is not None
    assert ub["id"] == admin_user.id
    assert ub["first_name"] == admin_user.first_name
    assert ub["last_name"] == admin_user.last_name
