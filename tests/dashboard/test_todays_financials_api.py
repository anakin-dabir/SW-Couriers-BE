"""API tests for GET /v1/dashboard/todays-financials."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.enums import PaymentRecordStatus
from app.modules.billing.models import BillingPayment
from app.modules.invoices.enums import InvoiceStatus, PaymentStatus
from app.modules.invoices.models import Invoice
from tests.dashboard.conftest import admin_headers, create_test_org

TODAYS_FINANCIALS = "/v1/dashboard/todays-financials"


@pytest.mark.asyncio
async def test_todays_financials_returns_trend_and_invoice_counts(
    client: AsyncClient,
    user_factory,
    db_session: AsyncSession,
) -> None:
    org = await create_test_org(db_session, reference="FIN001")
    admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    today = date.today()

    db_session.add(
        BillingPayment(
            payment_number="PAY-FIN-001",
            organization_id=org.id,
            amount=Decimal("100.00"),
            payment_date=today,
            status=PaymentRecordStatus.DEPOSITED.value,
            provider="CASH",
            allocation_status="UNALLOCATED",
            allocated_amount=Decimal("0.00"),
            unallocated_amount=Decimal("100.00"),
        )
    )
    db_session.add(
        Invoice(
            organization_id=org.id,
            issue_date=today,
            due_date=today,
            subtotal=Decimal("50.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("10.00"),
            total=Decimal("60.00"),
            paid_amount=Decimal("0"),
            payment_status=PaymentStatus.UNPAID.value,
            status=InvoiceStatus.SENT.value,
        )
    )
    db_session.add(
        Invoice(
            organization_id=org.id,
            issue_date=today,
            due_date=today - timedelta(days=3),
            subtotal=Decimal("80.00"),
            vat_rate=Decimal("20.00"),
            vat_amount=Decimal("16.00"),
            total=Decimal("96.00"),
            paid_amount=Decimal("0"),
            payment_status=PaymentStatus.UNPAID.value,
            status=InvoiceStatus.SENT.value,
        )
    )
    await db_session.flush()

    resp = await client.get(
        TODAYS_FINANCIALS,
        headers=admin_headers(admin.id),
        params={"organization_id": org.id},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert len(data["revenue_trend"]) == 7
    assert Decimal(data["revenue_today"]) >= Decimal("100.00")
    # Due-today UNPAID counts as unpaid; past-due UNPAID is classified as overdue only.
    assert data["unpaid_invoices_count"] >= 1
    assert data["overdue_invoices_count"] >= 1
