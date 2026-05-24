"""Edge-case tests for client inactivity policy and config."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import UserInactiveReason, UserStatus
from app.core.security import create_access_token
from app.modules.client_inactivity.constants import MAX_INACTIVE_AFTER_DAYS
from app.modules.client_inactivity.models import ClientInactivityConfig
from app.modules.client_inactivity.service import ClientInactivityService
from app.modules.organizations.enums import CompanySize, IndustryType, OrganizationStatus
from app.modules.organizations.models import Organization
from app.modules.user.models import User
from tests.conftest import TEST_PASSWORD

AUTH = "/v1/auth"
CONFIG = "/v1/client-inactivity-config"
B2B_HEADERS = {"X-Client-Type": "CUSTOMER_B2B"}


async def _create_org(db_session: AsyncSession) -> Organization:
    org = Organization(
        trading_name="Inactivity Edge Org",
        legal_entity_name="Inactivity Edge Org Ltd",
        industry=IndustryType.LOGISTICS_TRANSPORT,
        company_size=CompanySize.EMPLOYEES_11_50,
        date_of_incorporation=datetime.now(UTC).date(),
        companies_house_number="CH-INACT-EDGE",
        reg_address_line_1="1 Test Street",
        reg_city="London",
        reg_postcode="E1 1AA",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org)
    await db_session.flush()
    return org


def _customer_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="CUSTOMER_B2C", client_type="CUSTOMER_B2C")
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "CUSTOMER_B2C"}


@pytest.mark.asyncio
async def test_config_get_requires_admin(client: AsyncClient, user_factory) -> None:
    user = await user_factory(status="ACTIVE", email_verified=True, role="CUSTOMER_B2C")
    resp = await client.get(CONFIG, headers=_customer_headers(user.id))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_config_patch_empty_body_rejected(client: AsyncClient, admin_headers: dict) -> None:
    await client.get(CONFIG, headers=admin_headers)
    resp = await client.patch(CONFIG, headers=admin_headers, json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_config_patch_rejects_threshold_above_max(client: AsyncClient, admin_headers: dict) -> None:
    seeded = await client.get(CONFIG, headers=admin_headers)
    version = seeded.json()["data"]["version"]
    resp = await client.patch(
        CONFIG,
        headers=admin_headers,
        json={"inactive_after_days": MAX_INACTIVE_AFTER_DAYS + 1, "version": version},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_config_patch_without_seed_returns_not_found(client: AsyncClient, admin_headers: dict) -> None:
    resp = await client.patch(
        CONFIG,
        headers=admin_headers,
        json={"enabled": False},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_job_skips_active_b2b_user_within_threshold(db_session: AsyncSession, user_factory) -> None:
    org = await _create_org(db_session)
    recent_login = datetime.now(UTC) - timedelta(days=10)
    user = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        last_login=recent_login,
    )
    db_session.add(ClientInactivityConfig(enabled=True, inactive_after_days=60))
    await db_session.flush()

    result = await ClientInactivityService(db_session).run_daily_inactivity_job()
    assert result["marked_inactive"] == 0
    refreshed = await db_session.get(User, user.id)
    assert refreshed.status == UserStatus.ACTIVE


@pytest.mark.asyncio
async def test_job_uses_created_at_when_last_login_null(db_session: AsyncSession, user_factory) -> None:
    org = await _create_org(db_session)
    user = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        last_login=None,
    )
    user.created_at = datetime.now(UTC) - timedelta(days=120)
    db_session.add(ClientInactivityConfig(enabled=True, inactive_after_days=60))
    await db_session.flush()

    result = await ClientInactivityService(db_session).run_daily_inactivity_job()
    assert result["marked_inactive"] == 1


@pytest.mark.asyncio
async def test_job_does_not_mark_b2c_users(db_session: AsyncSession, user_factory) -> None:
    stale_login = datetime.now(UTC) - timedelta(days=120)
    user = await user_factory(
        role="CUSTOMER_B2C",
        status="ACTIVE",
        email_verified=True,
        last_login=stale_login,
    )
    db_session.add(ClientInactivityConfig(enabled=True, inactive_after_days=60))
    await db_session.flush()

    result = await ClientInactivityService(db_session).run_daily_inactivity_job()
    assert result["marked_inactive"] == 0
    refreshed = await db_session.get(User, user.id)
    assert refreshed.status == UserStatus.ACTIVE


@pytest.mark.asyncio
async def test_job_does_not_reprocess_already_inactive_users(db_session: AsyncSession, user_factory) -> None:
    org = await _create_org(db_session)
    user = await user_factory(
        role="CUSTOMER_B2B",
        status="INACTIVE",
        email_verified=True,
        organization_id=org.id,
        inactive_reason=UserInactiveReason.INACTIVITY.value,
        last_login=datetime.now(UTC) - timedelta(days=120),
    )
    db_session.add(ClientInactivityConfig(enabled=True, inactive_after_days=60))
    await db_session.flush()

    result = await ClientInactivityService(db_session).run_daily_inactivity_job()
    assert result["marked_inactive"] == 0


@pytest.mark.asyncio
async def test_inactivity_inactive_login_wrong_password_rejected(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await _create_org(db_session)
    user = await user_factory(
        role="CUSTOMER_B2B",
        status="INACTIVE",
        email_verified=True,
        organization_id=org.id,
        inactive_reason=UserInactiveReason.INACTIVITY.value,
    )
    resp = await client.post(
        f"{AUTH}/login",
        headers=B2B_HEADERS,
        json={"email": user.email, "password": "WrongPassword!123"},
    )
    assert resp.status_code == 401

    row = (await db_session.execute(select(User).where(User.id == user.id))).scalar_one()
    assert row.status == UserStatus.INACTIVE


@pytest.mark.asyncio
async def test_suspended_user_not_reactivated_via_inactivity_path(client: AsyncClient, user_factory) -> None:
    user = await user_factory(
        role="CUSTOMER_B2B",
        status="SUSPENDED",
        email_verified=True,
        inactive_reason=UserInactiveReason.INACTIVITY.value,
    )
    resp = await client.post(
        f"{AUTH}/login",
        headers=B2B_HEADERS,
        json={"email": user.email, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 401
    assert "suspended" in resp.json()["message"].lower()
