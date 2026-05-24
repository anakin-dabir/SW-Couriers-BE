"""API tests for org billing overview (GET /organizations/{id}/billing/overview)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.invoices.enums import InvoiceStatus, PaymentStatus
from app.modules.invoices.models import Invoice
from app.modules.organizations.models import Organization

OVERVIEW = "/v1/organizations/{org_id}/billing/overview"


def _admin_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="ADMIN", client_type="ADMIN")
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "ADMIN"}


def _b2b_headers(user_id: str, organization_id: str) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user_id,
        role="CUSTOMER_B2B",
        client_type="CUSTOMER_B2B",
        organization_id=organization_id,
    )
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "CUSTOMER_B2B"}


async def _create_org(db_session: AsyncSession, *, reference: str = "BOV001") -> Organization:
    org = Organization(
        reference=reference,
        trading_name="Billing Overview Org",
        legal_entity_name="Billing Overview Org Ltd",
        companies_house_number="CHBOV01",
        vat_number="GB222222222",
        date_of_incorporation=date(2020, 1, 1),
        industry="OTHER",
        company_size="1-10 employees",
        reg_address_line_1="1 Overview Lane",
        reg_city="Cardiff",
        reg_postcode="CF10 1AA",
        status="ACTIVE",
    )
    db_session.add(org)
    await db_session.flush()
    return org


@pytest.mark.asyncio
async def test_billing_overview_returns_kpis_and_charts_for_admin(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await _create_org(db_session)
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    today = date.today()
    inv = Invoice(
        organization_id=org.id,
        issue_date=today,
        due_date=today + timedelta(days=30),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        paid_amount=Decimal("0"),
        payment_status=PaymentStatus.UNPAID.value,
        status=InvoiceStatus.SENT.value,
    )
    db_session.add(inv)
    await db_session.flush()

    resp = await client.get(
        OVERVIEW.format(org_id=org.id),
        headers=_admin_headers(admin.id),
        params={"period": "last_30_days", "chart_year": today.year},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["meta"]["definitions_version"]
    assert data["meta"]["chart_year"] == today.year
    assert "total_billed" in data["kpis"]
    assert data["kpis"]["total_billed"]["value"] == "120.00"
    assert len(data["charts"]["revenue_trend"]) == 12
    assert len(data["charts"]["billing_activity"]) == 12


@pytest.mark.asyncio
async def test_billing_overview_rejects_b2b_role(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await _create_org(db_session, reference="BOV002")
    b2b = await user_factory(role="CUSTOMER_B2B", status="ACTIVE", email_verified=True, organization_id=org.id)

    resp = await client.get(
        OVERVIEW.format(org_id=org.id),
        headers=_b2b_headers(b2b.id, org.id),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_billing_overview_excludes_voided_from_total_billed(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await _create_org(db_session, reference="BOV003")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    today = date.today()
    sent = Invoice(
        organization_id=org.id,
        issue_date=today,
        due_date=today + timedelta(days=14),
        subtotal=Decimal("50.00"),
        vat_rate=Decimal("0"),
        vat_amount=Decimal("0"),
        total=Decimal("50.00"),
        status=InvoiceStatus.SENT.value,
        payment_status=PaymentStatus.UNPAID.value,
    )
    voided = Invoice(
        organization_id=org.id,
        issue_date=today,
        due_date=today + timedelta(days=14),
        subtotal=Decimal("200.00"),
        vat_rate=Decimal("0"),
        vat_amount=Decimal("0"),
        total=Decimal("200.00"),
        status=InvoiceStatus.SENT.value,
        payment_status=PaymentStatus.VOID.value,
    )
    db_session.add(sent)
    db_session.add(voided)
    await db_session.flush()

    resp = await client.get(
        OVERVIEW.format(org_id=org.id),
        headers=_admin_headers(admin.id),
        params={"period": "today"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["kpis"]["total_billed"]["value"] == "50.00"
