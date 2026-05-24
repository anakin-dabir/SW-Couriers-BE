"""Edge-case API tests for recurring account statement schedules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.account_statements.test_account_statements_api import (
    ORG_BASE,
    _admin_headers,
    _create_org,
)


@pytest.mark.asyncio
async def test_create_schedule_rejects_inverted_valid_window(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await _create_org(db_session)
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statement-schedules",
        headers=_admin_headers(admin.id),
        json={
            "frequency": "CUSTOM",
            "valid_from": "2027-06-01",
            "valid_to": "2027-01-01",
            "recipient_email": "billing@example.com",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_schedule_rejects_invalid_recipient_email(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await _create_org(db_session)
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statement-schedules",
        headers=_admin_headers(admin.id),
        json={
            "frequency": "MONTHLY_FIRST",
            "recipient_email": "not-an-email",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_monthly_rejects_interval_days(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await _create_org(db_session)
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statement-schedules",
        headers=_admin_headers(admin.id),
        json={
            "frequency": "MONTHLY_FIRST",
            "recipient_email": "billing@example.com",
            "interval_days": 30,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_custom_rejects_interval_below_minimum(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await _create_org(db_session)
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statement-schedules",
        headers=_admin_headers(admin.id),
        json={
            "frequency": "CUSTOM",
            "valid_from": "2027-01-01",
            "valid_to": "2027-12-31",
            "recipient_email": "billing@example.com",
            "interval_days": 3,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_custom_past_valid_to_is_completed_without_next_run(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await _create_org(db_session)
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statement-schedules",
        headers=_admin_headers(admin.id),
        json={
            "frequency": "CUSTOM",
            "valid_from": "2020-01-01",
            "valid_to": "2020-03-31",
            "recipient_email": "billing@example.com",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["next_run_at"] is None
    assert data["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_list_schedules_returns_created_entries(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await _create_org(db_session)
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    create = await client.post(
        f"{ORG_BASE}/{org.id}/account-statement-schedules",
        headers=_admin_headers(admin.id),
        json={
            "frequency": "QUARTERLY",
            "recipient_email": "billing@example.com",
        },
    )
    assert create.status_code == 201

    listed = await client.get(
        f"{ORG_BASE}/{org.id}/account-statement-schedules",
        headers=_admin_headers(admin.id),
    )
    assert listed.status_code == 200
    items = listed.json()["data"]
    assert len(items) >= 1
    assert any(item["frequency"] == "QUARTERLY" for item in items)


@pytest.mark.asyncio
async def test_create_schedule_unknown_org_returns_404(
    client: AsyncClient,
    user_factory,
) -> None:
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.post(
        f"{ORG_BASE}/00000000-0000-0000-0000-000000000099/account-statement-schedules",
        headers=_admin_headers(admin.id),
        json={
            "frequency": "MONTHLY_FIRST",
            "recipient_email": "billing@example.com",
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_schedule_rejects_unknown_timezone(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await _create_org(db_session)
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    resp = await client.post(
        f"{ORG_BASE}/{org.id}/account-statement-schedules",
        headers=_admin_headers(admin.id),
        json={
            "frequency": "MONTHLY_FIRST",
            "recipient_email": "billing@example.com",
            "timezone": "Not/A_Timezone",
        },
    )
    assert resp.status_code == 422
