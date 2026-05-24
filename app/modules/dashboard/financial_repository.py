"""Financial aggregates for the admin dashboard."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.enums import PaymentRecordStatus
from app.modules.billing.models import BillingPayment
from app.modules.invoices.enums import InvoiceStatus, PaymentStatus
from app.modules.invoices.models import Invoice


class DashboardFinancialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _payment_filters(self, organization_id: str | None, start: date, end: date):
        filters = [
            BillingPayment.status != PaymentRecordStatus.VOIDED.value,
            BillingPayment.payment_date >= start,
            BillingPayment.payment_date <= end,
        ]
        if organization_id:
            filters.append(BillingPayment.organization_id == organization_id)
        return filters

    async def payments_total(self, organization_id: str | None, *, start: date, end: date) -> Decimal:
        stmt = select(func.coalesce(func.sum(BillingPayment.amount), 0)).where(
            and_(*self._payment_filters(organization_id, start, end))
        )
        return Decimal(str((await self._session.execute(stmt)).scalar_one() or 0))

    async def payments_by_day(
        self,
        organization_id: str | None,
        *,
        start: date,
        end: date,
    ) -> dict[date, Decimal]:
        stmt = (
            select(
                BillingPayment.payment_date,
                func.coalesce(func.sum(BillingPayment.amount), 0),
            )
            .where(and_(*self._payment_filters(organization_id, start, end)))
            .group_by(BillingPayment.payment_date)
        )
        rows = (await self._session.execute(stmt)).all()
        return {row[0]: Decimal(str(row[1])) for row in rows}

    async def invoice_collection_counts(self, organization_id: str | None, *, as_of: date) -> tuple[int, int]:
        """Return (unpaid_count, overdue_count) for sent invoices still open."""
        payment_status_expr = case(
            (
                and_(
                    Invoice.payment_status == PaymentStatus.UNPAID.value,
                    Invoice.due_date < as_of,
                ),
                PaymentStatus.OVERDUE.value,
            ),
            else_=Invoice.payment_status,
        )
        filters = [
            Invoice.status == InvoiceStatus.SENT.value,
            Invoice.payment_status.notin_(
                [PaymentStatus.VOID.value, PaymentStatus.WRITTEN_OFF.value, PaymentStatus.PAID.value]
            ),
        ]
        if organization_id:
            filters.append(Invoice.organization_id == organization_id)

        stmt = select(
            func.coalesce(
                func.sum(
                    case(
                        (payment_status_expr.in_([PaymentStatus.UNPAID.value, PaymentStatus.PARTIALLY_PAID.value]), 1),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(case((payment_status_expr == PaymentStatus.OVERDUE.value, 1), else_=0)),
                0,
            ),
        ).where(and_(*filters))
        row = (await self._session.execute(stmt)).one()
        return int(row[0] or 0), int(row[1] or 0)
