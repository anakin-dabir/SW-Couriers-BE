from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.invoices.models import Invoice
from tests.invoices.conftest import purge_invoice_domain

INVOICES = "/v1/invoices"


def _b2c_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="CUSTOMER_B2C", client_type="CUSTOMER_B2C")
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "CUSTOMER_B2C",
    }


@pytest.mark.asyncio
async def test_b2c_invoice_list_is_customer_scoped(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    await purge_invoice_domain(db_session)

    owner = await user_factory(role="CUSTOMER_B2C", status="ACTIVE", email_verified=True)
    foreign = await user_factory(role="CUSTOMER_B2C", status="ACTIVE", email_verified=True)
    today = date.today()
    db_session.add(
        Invoice(
            invoice_number="INV-920001",
            customer_id=owner.id,
            issue_date=today,
            due_date=today + timedelta(days=10),
            subtotal=Decimal("100.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("20.00"),
            total=Decimal("120.00"),
            status="SENT",
        )
    )
    db_session.add(
        Invoice(
            invoice_number="INV-920002",
            customer_id=foreign.id,
            issue_date=today,
            due_date=today + timedelta(days=10),
            subtotal=Decimal("50.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("10.00"),
            total=Decimal("60.00"),
            status="SENT",
        )
    )
    await db_session.flush()

    resp = await client.get(INVOICES, headers=_b2c_headers(owner.id))
    assert resp.status_code == 200, resp.text
    numbers = {item["invoice_number"] for item in resp.json()["data"]["items"]}
    assert "INV-920001" in numbers
    assert "INV-920002" not in numbers


@pytest.mark.asyncio
async def test_b2c_invoice_detail_cannot_access_foreign_invoice(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    await purge_invoice_domain(db_session)

    owner = await user_factory(role="CUSTOMER_B2C", status="ACTIVE", email_verified=True)
    foreign = await user_factory(role="CUSTOMER_B2C", status="ACTIVE", email_verified=True)
    today = date.today()
    own_invoice = Invoice(
        invoice_number="INV-920003",
        customer_id=owner.id,
        issue_date=today,
        due_date=today + timedelta(days=10),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
    )
    foreign_invoice = Invoice(
        invoice_number="INV-920004",
        customer_id=foreign.id,
        issue_date=today,
        due_date=today + timedelta(days=10),
        subtotal=Decimal("80.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("16.00"),
        total=Decimal("96.00"),
        status="SENT",
    )
    db_session.add(own_invoice)
    db_session.add(foreign_invoice)
    await db_session.flush()

    own_resp = await client.get(f"{INVOICES}/{own_invoice.id}", headers=_b2c_headers(owner.id))
    assert own_resp.status_code == 200, own_resp.text

    foreign_resp = await client.get(f"{INVOICES}/{foreign_invoice.id}", headers=_b2c_headers(owner.id))
    assert foreign_resp.status_code == 404
