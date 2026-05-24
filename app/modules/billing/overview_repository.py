"""Read-only aggregates for org billing overview."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.enums import PaymentRecordStatus, RefundStatus
from app.modules.billing.models import BillingPayment, Refund
from app.modules.invoices.enums import CreditNoteStatus, InvoiceStatus, PaymentStatus
from app.modules.invoices.models import CreditNote, Invoice


@dataclass(frozen=True, slots=True)
class PeriodMoney:
    amount: Decimal
    count: int = 0


class BillingOverviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def total_billed(self, *, organization_id: str, start: date, end: date) -> PeriodMoney:
        stmt = select(
            func.coalesce(func.sum(Invoice.total), 0),
            func.count(Invoice.id),
        ).where(
            Invoice.organization_id == organization_id,
            Invoice.status == InvoiceStatus.SENT.value,
            Invoice.payment_status.notin_([PaymentStatus.VOID.value, PaymentStatus.WRITTEN_OFF.value]),
            Invoice.issue_date >= start,
            Invoice.issue_date <= end,
        )
        row = (await self._session.execute(stmt)).one()
        return PeriodMoney(Decimal(str(row[0])), int(row[1] or 0))

    async def payments_received(self, *, organization_id: str, start: date, end: date) -> PeriodMoney:
        stmt = select(
            func.coalesce(func.sum(BillingPayment.amount), 0),
            func.count(BillingPayment.id),
        ).where(
            BillingPayment.organization_id == organization_id,
            BillingPayment.status != PaymentRecordStatus.VOIDED.value,
            BillingPayment.payment_date >= start,
            BillingPayment.payment_date <= end,
        )
        row = (await self._session.execute(stmt)).one()
        return PeriodMoney(Decimal(str(row[0])), int(row[1] or 0))

    async def outstanding_and_overdue_as_of(self, *, organization_id: str, as_of: date) -> tuple[Decimal, Decimal]:
        outstanding_expr = case(
            (
                Invoice.payment_status.in_([PaymentStatus.VOID.value, PaymentStatus.WRITTEN_OFF.value]),
                0,
            ),
            else_=func.greatest(Invoice.total - Invoice.paid_amount, 0),
        )
        overdue_expr = case(
            (
                and_(
                    Invoice.payment_status == PaymentStatus.OVERDUE.value,
                    Invoice.status == InvoiceStatus.SENT.value,
                ),
                func.greatest(Invoice.total - Invoice.paid_amount, 0),
            ),
            else_=0,
        )
        stmt = select(
            func.coalesce(func.sum(outstanding_expr), 0),
            func.coalesce(func.sum(overdue_expr), 0),
        ).where(
            Invoice.organization_id == organization_id,
            Invoice.status == InvoiceStatus.SENT.value,
            Invoice.issue_date <= as_of,
        )
        row = (await self._session.execute(stmt)).one()
        return Decimal(str(row[0])), Decimal(str(row[1]))

    async def credit_notes_issued_count(self, *, organization_id: str, start: date, end: date) -> int:
        stmt = select(func.count(CreditNote.id)).where(
            CreditNote.organization_id == organization_id,
            CreditNote.status == CreditNoteStatus.ISSUED.value,
            CreditNote.issue_date >= start,
            CreditNote.issue_date <= end,
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def refunds_issued(self, *, organization_id: str, start: date, end: date) -> PeriodMoney:
        stmt = select(
            func.coalesce(func.sum(Refund.processed_amount), 0),
            func.count(Refund.id),
        ).where(
            Refund.organization_id == organization_id,
            Refund.status == RefundStatus.COMPLETED.value,
            Refund.completed_at.isnot(None),
            func.date(Refund.completed_at) >= start,
            func.date(Refund.completed_at) <= end,
        )
        row = (await self._session.execute(stmt)).one()
        return PeriodMoney(Decimal(str(row[0])), int(row[1] or 0))

    async def revenue_by_month(self, *, organization_id: str, year: int) -> list[dict]:
        billed = await self._monthly_invoice_totals(organization_id=organization_id, year=year)
        refunds = await self._monthly_refund_totals(organization_id=organization_id, year=year)
        out = []
        for month in range(1, 13):
            rev = billed.get(month, Decimal("0"))
            ref = refunds.get(month, Decimal("0"))
            out.append(
                {
                    "month": month,
                    "revenue": rev,
                    "refunds": ref,
                    "net_revenue": (rev - ref).quantize(Decimal("0.01")),
                }
            )
        return out

    async def _monthly_invoice_totals(self, *, organization_id: str, year: int) -> dict[int, Decimal]:
        stmt = (
            select(
                func.extract("month", Invoice.issue_date).label("m"),
                func.coalesce(func.sum(Invoice.total), 0),
            )
            .where(
                Invoice.organization_id == organization_id,
                Invoice.status == InvoiceStatus.SENT.value,
                Invoice.payment_status.notin_([PaymentStatus.VOID.value, PaymentStatus.WRITTEN_OFF.value]),
                func.extract("year", Invoice.issue_date) == year,
            )
            .group_by("m")
        )
        rows = (await self._session.execute(stmt)).all()
        return {int(r[0]): Decimal(str(r[1])) for r in rows}

    async def _monthly_refund_totals(self, *, organization_id: str, year: int) -> dict[int, Decimal]:
        stmt = (
            select(
                func.extract("month", Refund.completed_at).label("m"),
                func.coalesce(func.sum(Refund.processed_amount), 0),
            )
            .where(
                Refund.organization_id == organization_id,
                Refund.status == RefundStatus.COMPLETED.value,
                Refund.completed_at.isnot(None),
                func.extract("year", Refund.completed_at) == year,
            )
            .group_by("m")
        )
        rows = (await self._session.execute(stmt)).all()
        return {int(r[0]): Decimal(str(r[1])) for r in rows}

    async def payment_method_buckets(self, *, organization_id: str, start: date, end: date) -> dict[str, Decimal]:
        stmt = (
            select(BillingPayment.provider, func.coalesce(func.sum(BillingPayment.amount), 0))
            .where(
                BillingPayment.organization_id == organization_id,
                BillingPayment.status != PaymentRecordStatus.VOIDED.value,
                BillingPayment.payment_date >= start,
                BillingPayment.payment_date <= end,
            )
            .group_by(BillingPayment.provider)
        )
        rows = (await self._session.execute(stmt)).all()
        buckets: dict[str, Decimal] = {"CARD": Decimal("0"), "BANK_TRANSFER": Decimal("0"), "CASH": Decimal("0")}
        from app.modules.billing.metrics import map_payment_provider_to_chart_bucket

        for provider, amount in rows:
            key = map_payment_provider_to_chart_bucket(str(provider))
            buckets[key] = buckets.get(key, Decimal("0")) + Decimal(str(amount))
        return buckets

    async def invoice_status_breakdown(self, *, organization_id: str, year: int) -> list[dict]:
        stmt = (
            select(
                Invoice.payment_status,
                func.count(Invoice.id),
                func.coalesce(func.sum(Invoice.total), 0),
            )
            .where(
                Invoice.organization_id == organization_id,
                Invoice.status == InvoiceStatus.SENT.value,
                func.extract("year", Invoice.issue_date) == year,
            )
            .group_by(Invoice.payment_status)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "status": str(status),
                "count": int(count or 0),
                "total_value": Decimal(str(total or 0)),
            }
            for status, count, total in rows
        ]

    async def billing_activity_by_month(self, *, organization_id: str, year: int) -> list[dict]:
        inv = await self._monthly_invoice_totals(organization_id=organization_id, year=year)
        pay_stmt = (
            select(
                func.extract("month", BillingPayment.payment_date).label("m"),
                func.coalesce(func.sum(BillingPayment.amount), 0),
                func.count(BillingPayment.id),
            )
            .where(
                BillingPayment.organization_id == organization_id,
                BillingPayment.status != PaymentRecordStatus.VOIDED.value,
                func.extract("year", BillingPayment.payment_date) == year,
            )
            .group_by("m")
        )
        pay_rows = (await self._session.execute(pay_stmt)).all()
        pay_amounts = {int(r[0]): Decimal(str(r[1])) for r in pay_rows}
        pay_counts = {int(r[0]): int(r[2] or 0) for r in pay_rows}
        inv_counts_stmt = (
            select(
                func.extract("month", Invoice.issue_date).label("m"),
                func.count(Invoice.id),
            )
            .where(
                Invoice.organization_id == organization_id,
                Invoice.status == InvoiceStatus.SENT.value,
                func.extract("year", Invoice.issue_date) == year,
            )
            .group_by("m")
        )
        inv_count_rows = (await self._session.execute(inv_counts_stmt)).all()
        inv_counts = {int(r[0]): int(r[1] or 0) for r in inv_count_rows}
        result = []
        for month in range(1, 13):
            result.append(
                {
                    "month": month,
                    "invoices_amount": inv.get(month, Decimal("0")),
                    "invoices_count": inv_counts.get(month, 0),
                    "payments_amount": pay_amounts.get(month, Decimal("0")),
                    "payments_count": pay_counts.get(month, 0),
                }
            )
        return result
