"""Shared fixtures for dashboard module tests."""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.organizations.models import Organization


DASHBOARD_BASE = "/v1/dashboard"


def admin_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="ADMIN", client_type="ADMIN")
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "ADMIN"}


def b2b_headers(user_id: str, organization_id: str) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user_id,
        role="CUSTOMER_B2B",
        client_type="CUSTOMER_B2B",
        organization_id=organization_id,
    )
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "CUSTOMER_B2B"}


async def create_test_org(db_session: AsyncSession, *, reference: str = "DASHORG") -> Organization:
    org = Organization(
        reference=reference,
        trading_name="Dashboard Test Org",
        legal_entity_name="Dashboard Test Org Ltd",
        companies_house_number="CHDASHORG",
        vat_number="GB333333333",
        date_of_incorporation=date(2020, 1, 1),
        industry="OTHER",
        company_size="1-10 employees",
        reg_address_line_1="1 Dashboard Lane",
        reg_city="London",
        reg_postcode="E1 1AA",
        status="ACTIVE",
    )
    db_session.add(org)
    await db_session.flush()
    return org
