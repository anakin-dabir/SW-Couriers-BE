from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.modules.org_credit.enums import OrgCreditLedgerMovementType
from app.modules.org_credit.models import OrgCreditAccount, OrgCreditLedgerEntry
from app.modules.organizations.models import Organization


@pytest.mark.asyncio
async def test_get_utilisation_rejects_date_from_after_date_to(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
) -> None:
    org: Organization = await org_factory()
    r = await client.get(
        f"/v1/organizations/{org.id}/credit/monitoring/utilisation",
        headers=admin_headers,
        params={"date_from": "2026-03-20", "date_to": "2026-03-01"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_utilisation_includes_payment_and_ageing_placeholders(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    acct = OrgCreditAccount(
        organization_id=org.id,
        credit_limit=Decimal("10000.00"),
        used_credit=Decimal("0"),
    )
    db_session.add(acct)
    await db_session.flush()

    r = await client.get(
        f"/v1/organizations/{org.id}/credit/monitoring/utilisation",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["payment_behaviour"]["summary"] is None
    assert data["ageing"]["as_of"] is None
    assert len(data["ageing_buckets"]) == 4
    labels = {b["label"] for b in data["ageing_buckets"]}
    assert labels == {"0-30", "31-60", "61-90", "90+"}


@pytest.mark.asyncio
async def test_get_utilisation_history_respects_date_range(
    client: AsyncClient,
    admin_headers: dict[str, str],
    org_factory,
    db_session,
) -> None:
    org: Organization = await org_factory()
    acct = OrgCreditAccount(
        organization_id=org.id,
        credit_limit=Decimal("50000.00"),
        used_credit=Decimal("41250.00"),
    )
    db_session.add(acct)
    await db_session.flush()

    times = [
        (datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC), Decimal("30500.00"), "k-a"),
        (datetime(2026, 3, 10, 10, 0, 0, tzinfo=UTC), Decimal("35000.00"), "k-b"),
        (datetime(2026, 3, 14, 10, 0, 0, tzinfo=UTC), Decimal("41250.00"), "k-c"),
    ]
    for ts, used, key in times:
        entry = OrgCreditLedgerEntry(
            organization_id=org.id,
            account_id=acct.id,
            movement_type=OrgCreditLedgerMovementType.MANUAL_ADJUST_USED,
            source_type=None,
            source_id=None,
            idempotency_key=f"util-test-{key}-{org.id}",
            used_credit_after=used,
            available_credit_after=Decimal("50000.00") - used,
            credit_limit_after=Decimal("50000.00"),
            adjustment_reason=None,
            actor_user_id=None,
            created_at=ts,
        )
        db_session.add(entry)
    await db_session.flush()

    r_all = await client.get(
        f"/v1/organizations/{org.id}/credit/monitoring/utilisation",
        headers=admin_headers,
        params={"page": 1, "size": 20},
    )
    assert r_all.status_code == 200, r_all.text
    assert r_all.json()["data"]["history_total"] == 3

    r_filtered = await client.get(
        f"/v1/organizations/{org.id}/credit/monitoring/utilisation",
        headers=admin_headers,
        params={
            "page": 1,
            "size": 20,
            "date_from": "2026-03-10",
            "date_to": "2026-03-14",
        },
    )
    assert r_filtered.status_code == 200, r_filtered.text
    assert r_filtered.json()["data"]["history_total"] == 2
