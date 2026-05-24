"""API tests for recurring account statement schedules."""

from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.account_statements.test_account_statements_api import (
    ORG_BASE,
    _admin_headers,
    _create_org,
)


@pytest.mark.asyncio
async def test_create_monthly_schedule_sets_next_run(
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
            "valid_from": "2026-01-01",
            "valid_to": "2027-12-31",
            "recipient_email": "billing@example.com",
            "timezone": "Europe/London",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["frequency"] == "MONTHLY_FIRST"
    assert data["status"] == "ACTIVE"
    assert data["next_run_at"] is not None


@pytest.mark.asyncio
async def test_create_monthly_schedule_without_valid_window(
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
            "timezone": "Europe/London",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["frequency"] == "MONTHLY_FIRST"
    assert data["valid_to"] == "2099-12-31"
    assert data["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_create_quarterly_schedule_without_valid_window(
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
            "frequency": "QUARTERLY",
            "recipient_email": "billing@example.com",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["frequency"] == "QUARTERLY"


@pytest.mark.asyncio
async def test_create_custom_schedule_without_interval_days(
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
            "valid_to": "2027-03-31",
            "recipient_email": "billing@example.com",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["frequency"] == "CUSTOM"
    assert data["interval_days"] is None
    assert data["valid_from"] == "2027-01-01"
    assert data["valid_to"] == "2027-03-31"
    assert data["next_run_at"] is not None


@pytest.mark.asyncio
async def test_create_custom_schedule_requires_valid_window(
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
            "recipient_email": "billing@example.com",
            "interval_days": 30,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_schedule_rejects_invalid_frequency(
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
            "frequency": "WEEKLY",
            "valid_from": "2026-01-01",
            "valid_to": "2027-12-31",
            "recipient_email": "billing@example.com",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_custom_schedule_with_interval_days(
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
            "valid_from": "2026-01-01",
            "valid_to": "2027-12-31",
            "recipient_email": "billing@example.com",
            "interval_days": 30,
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["interval_days"] == 30
