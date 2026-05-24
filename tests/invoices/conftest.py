"""Shared invoice test helpers."""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.models import BillingPayment, BillingPaymentAllocation, BillingPaymentEvent, Refund
from app.modules.invoices.models import (
    CreditNote,
    Invoice,
    InvoiceCreditApplication,
    InvoiceEvent,
    InvoiceLineItem,
    InvoicePdfArtifact,
)


async def purge_invoice_domain(session: AsyncSession) -> None:
    """Delete invoice-domain rows in FK-safe order for isolated list/scope tests."""
    await session.execute(delete(BillingPaymentAllocation))
    await session.execute(delete(Refund))
    await session.execute(delete(BillingPaymentEvent))
    await session.execute(delete(BillingPayment))
    await session.execute(delete(InvoiceCreditApplication))
    await session.execute(delete(CreditNote))
    await session.execute(delete(InvoicePdfArtifact))
    await session.execute(delete(InvoiceEvent))
    await session.execute(delete(InvoiceLineItem))
    await session.execute(delete(Invoice))
    await session.flush()
