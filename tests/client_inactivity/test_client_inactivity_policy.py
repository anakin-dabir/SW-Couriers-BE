"""Client inactivity policy — job and login reactivation."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import UserInactiveReason, UserStatus
from app.modules.client_inactivity.models import ClientInactivityConfig
from app.modules.client_inactivity.service import ClientInactivityService
from app.modules.organizations.enums import CompanySize, IndustryType, OrganizationStatus
from app.modules.organizations.models import Organization
from app.modules.user.models import User
from tests.conftest import TEST_PASSWORD

AUTH = "/v1/auth"
B2B_HEADERS = {"X-Client-Type": "CUSTOMER_B2B"}


async def _create_org(db_session: AsyncSession) -> Organization:
    org = Organization(
        trading_name="Inactivity Org",
        legal_entity_name="Inactivity Org Ltd",
        industry=IndustryType.LOGISTICS_TRANSPORT,
        company_size=CompanySize.EMPLOYEES_11_50,
        date_of_incorporation=datetime.now(UTC).date(),
        companies_house_number="CH-INACT-001",
        reg_address_line_1="1 Test Street",
        reg_city="London",
        reg_postcode="E1 1AA",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org)
    await db_session.flush()
    return org


@pytest.mark.asyncio
async def test_daily_job_marks_stale_b2b_user_inactive(db_session: AsyncSession, user_factory) -> None:
    org = await _create_org(db_session)
    stale_login = datetime.now(UTC) - timedelta(days=90)
    user = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        last_login=stale_login,
    )
    db_session.add(
        ClientInactivityConfig(
            enabled=True,
            inactive_after_days=60,
        )
    )
    await db_session.flush()

    service = ClientInactivityService(db_session)
    result = await service.run_daily_inactivity_job(today=datetime.now(UTC).date())

    assert result["marked_inactive"] == 1
    refreshed = await db_session.get(User, user.id)
    assert refreshed is not None
    assert refreshed.status == UserStatus.INACTIVE
    assert refreshed.inactive_reason == UserInactiveReason.INACTIVITY.value
    assert refreshed.inactivated_at is not None


@pytest.mark.asyncio
async def test_daily_job_skips_when_disabled(db_session: AsyncSession, user_factory) -> None:
    org = await _create_org(db_session)
    stale_login = datetime.now(UTC) - timedelta(days=90)
    user = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
        last_login=stale_login,
    )
    db_session.add(
        ClientInactivityConfig(
            enabled=False,
            inactive_after_days=60,
        )
    )
    await db_session.flush()

    service = ClientInactivityService(db_session)
    result = await service.run_daily_inactivity_job(today=datetime.now(UTC).date())

    assert result["marked_inactive"] == 0
    refreshed = await db_session.get(User, user.id)
    assert refreshed.status == UserStatus.ACTIVE


@pytest.mark.asyncio
async def test_login_reactivates_inactivity_inactive_b2b_user(
    client: AsyncClient,
    db_session: AsyncSession,
    user_factory,
) -> None:
    org = await _create_org(db_session)
    user = await user_factory(
        role="CUSTOMER_B2B",
        status="INACTIVE",
        email_verified=True,
        organization_id=org.id,
        inactive_reason=UserInactiveReason.INACTIVITY.value,
        inactivated_at=datetime.now(UTC) - timedelta(days=1),
    )

    resp = await client.post(
        f"{AUTH}/login",
        headers=B2B_HEADERS,
        json={"email": user.email, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200, resp.text

    row = (await db_session.execute(select(User).where(User.id == user.id))).scalar_one()
    assert row.status == UserStatus.ACTIVE
    assert row.inactive_reason is None
    assert row.inactivated_at is None


@pytest.mark.asyncio
async def test_login_still_rejects_manual_inactive_user(client: AsyncClient, user_factory) -> None:
    user = await user_factory(status="INACTIVE", email_verified=True, role="CUSTOMER_B2C")
    resp = await client.post(
        f"{AUTH}/login",
        headers={"X-Client-Type": "CUSTOMER_B2C"},
        json={"email": user.email, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 401
