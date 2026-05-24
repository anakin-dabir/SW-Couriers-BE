"""Statement ledger computation (QB-inspired, in-app)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.schemas import quantize_currency
from app.modules.account_statements.constants import MAX_LEDGER_ROWS
from app.modules.account_statements.enums import StatementRowType
from app.modules.billing.enums import PaymentRecordStatus, RefundStatus
from app.modules.billing.models import BillingPayment, Refund
from app.modules.invoices.enums import CreditNoteStatus, InvoiceStatus, PaymentStatus
from app.modules.invoices.models import CreditNote, Invoice


@dataclass(frozen=True)
class StatementLineItemDetail:
    description: str
    quantity: int
    unit_price: str
    total_price: str


@dataclass
class LedgerRow:
    row_type: str
    reference_id: str
    reference_number: str
    issue_date: date
    payment_date: date | None
    order_ref: str | None
    status: str
    amount: Decimal
    display_amount: Decimal
    line_items: list[StatementLineItemDetail] = field(default_factory=list)


@dataclass
class StatementLedgerResult:
    opening_balance: Decimal
    closing_balance: Decimal
    rows: list[LedgerRow]
    total_invoice_amount: Decimal
    total_paid: Decimal
    total_unpaid: Decimal
    total_overdue: Decimal
    aging: dict[str, str]
    truncated: bool
    currency: str = "GBP"


def _q(value: Decimal | int | float | str) -> Decimal:
    return quantize_currency(value)


def _invoice_outstanding(inv: Invoice) -> Decimal:
    if inv.payment_status in {PaymentStatus.VOID.value, PaymentStatus.WRITTEN_OFF.value}:
        return _q(0)
    return _q(max(Decimal("0"), Decimal(inv.total) - Decimal(inv.paid_amount)))


def _signed_invoice_amount(inv: Invoice) -> Decimal:
    return _q(inv.total)


def _signed_payment_amount(payment: BillingPayment) -> Decimal:
    return _q(-payment.amount)


def _signed_credit_note_amount(cn: CreditNote) -> Decimal:
    return _q(-cn.total_credit_amount)


def _signed_refund_amount(refund: Refund) -> Decimal:
    return _q(refund.processed_amount)


def _aging_bucket(days_past_due: int) -> str | None:
    if days_past_due < 1:
        return None
    if days_past_due <= 30:
        return "days_1_30"
    if days_past_due <= 60:
        return "days_31_60"
    if days_past_due <= 90:
        return "days_61_90"
    return "days_90_plus"


def compute_content_signature(
    *,
    organization_id: str,
    period_start: date,
    period_end: date,
    include_line_item_detail: bool,
    include_credit_notes: bool,
    include_payment_history: bool,
    template_version: str,
) -> str:
    payload = {
        "organization_id": organization_id,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "include_line_item_detail": include_line_item_detail,
        "include_credit_notes": include_credit_notes,
        "include_payment_history": include_payment_history,
        "template_version": template_version,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class StatementLedgerBuilder:
    """Build chronological AR ledger rows and summary figures for an organization."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build(
        self,
        *,
        organization_id: str,
        period_start: date,
        period_end: date,
        include_line_item_detail: bool,
        include_credit_notes: bool,
        include_payment_history: bool,
        aging_as_of: date,
    ) -> StatementLedgerResult:
        movements = await self._load_movements(organization_id)
        currency = movements.get("currency", "GBP")

        ledger_entries: list[tuple[date, int, LedgerRow]] = []
        priority = {
            StatementRowType.INVOICE: 0,
            StatementRowType.CREDIT_NOTE: 1,
            StatementRowType.PAYMENT: 2,
            StatementRowType.REFUND: 3,
        }

        for inv in movements["invoices"]:
            row = self._invoice_row(inv, include_line_item_detail=include_line_item_detail)
            ledger_entries.append((inv.issue_date, priority[StatementRowType.INVOICE], row))

        if include_credit_notes:
            for cn in movements["credit_notes"]:
                row = self._credit_note_row(cn)
                ledger_entries.append((cn.issue_date, priority[StatementRowType.CREDIT_NOTE], row))

        if include_payment_history:
            for payment in movements["payments"]:
                row = self._payment_row(payment)
                ledger_entries.append((payment.payment_date, priority[StatementRowType.PAYMENT], row))

            for refund in movements["refunds"]:
                refund_date = refund.completed_at.date() if refund.completed_at else None
                if refund_date is None:
                    continue
                row = self._refund_row(refund)
                ledger_entries.append((refund_date, priority[StatementRowType.REFUND], row))

        ledger_entries.sort(key=lambda item: (item[0], item[1], item[2].reference_number))

        opening = _q(0)
        for entry_date, _, row in ledger_entries:
            if entry_date < period_start:
                opening += row.amount

        in_period: list[LedgerRow] = []
        truncated = False
        for entry_date, _, row in ledger_entries:
            if period_start <= entry_date <= period_end:
                if len(in_period) >= MAX_LEDGER_ROWS:
                    truncated = True
                    break
                in_period.append(row)

        running = opening
        for row in in_period:
            running += row.amount
            row.display_amount = row.amount

        closing = opening
        for entry_date, _, row in ledger_entries:
            if period_start <= entry_date <= period_end:
                closing += row.amount

        total_invoice_amount = _q(
            sum((r.display_amount for r in in_period if r.row_type == StatementRowType.INVOICE), Decimal("0"))
        )
        total_paid = _q(
            sum((-r.display_amount for r in in_period if r.row_type == StatementRowType.PAYMENT), Decimal("0"))
        )

        open_invoices = movements["open_invoices_as_of"]
        total_unpaid = _q(sum((_invoice_outstanding(inv) for inv in open_invoices), Decimal("0")))
        total_overdue = _q(
            sum(
                (
                    _invoice_outstanding(inv)
                    for inv in open_invoices
                    if inv.due_date < aging_as_of and _invoice_outstanding(inv) > 0
                ),
                Decimal("0"),
            )
        )

        aging_totals = {
            "days_1_30": Decimal("0"),
            "days_31_60": Decimal("0"),
            "days_61_90": Decimal("0"),
            "days_90_plus": Decimal("0"),
        }
        for inv in open_invoices:
            outstanding = _invoice_outstanding(inv)
            if outstanding <= 0:
                continue
            days_past = (aging_as_of - inv.due_date).days
            bucket = _aging_bucket(days_past)
            if bucket:
                aging_totals[bucket] += outstanding

        aging = {key: str(_q(val)) for key, val in aging_totals.items()}

        return StatementLedgerResult(
            opening_balance=opening,
            closing_balance=closing,
            rows=in_period,
            total_invoice_amount=total_invoice_amount,
            total_paid=total_paid,
            total_unpaid=total_unpaid,
            total_overdue=total_overdue,
            aging=aging,
            truncated=truncated,
            currency=currency,
        )

    async def _load_movements(self, organization_id: str) -> dict[str, Any]:
        inv_stmt: Select = (
            select(Invoice)
            .where(
                Invoice.organization_id == organization_id,
                Invoice.status == InvoiceStatus.SENT.value,
                Invoice.payment_status.notin_([PaymentStatus.VOID.value, PaymentStatus.WRITTEN_OFF.value]),
            )
            .options(selectinload(Invoice.line_items), selectinload(Invoice.order))
        )
        invoices = list((await self._session.execute(inv_stmt)).scalars().all())

        cn_stmt = select(CreditNote).where(
            CreditNote.organization_id == organization_id,
            CreditNote.status == CreditNoteStatus.ISSUED.value,
        )
        credit_notes = list((await self._session.execute(cn_stmt)).scalars().all())

        pay_stmt = select(BillingPayment).where(
            BillingPayment.organization_id == organization_id,
            BillingPayment.status != PaymentRecordStatus.VOIDED.value,
        )
        payments = list((await self._session.execute(pay_stmt)).scalars().all())

        refund_stmt = select(Refund).where(
            Refund.organization_id == organization_id,
            Refund.status == RefundStatus.COMPLETED.value,
            Refund.completed_at.isnot(None),
        )
        refunds = list((await self._session.execute(refund_stmt)).scalars().all())

        currency = invoices[0].currency if invoices else "GBP"
        return {
            "invoices": invoices,
            "credit_notes": credit_notes,
            "payments": payments,
            "refunds": refunds,
            "open_invoices_as_of": invoices,
            "currency": currency,
        }

    def _invoice_row(self, inv: Invoice, *, include_line_item_detail: bool) -> LedgerRow:
        order = getattr(inv, "order", None)
        order_ref = getattr(order, "order_id", None) if order else None
        line_items: list[StatementLineItemDetail] = []
        if include_line_item_detail and inv.line_items:
            for li in inv.line_items[:200]:
                line_items.append(
                    StatementLineItemDetail(
                        description=getattr(li, "description", "") or "",
                        quantity=int(getattr(li, "quantity", 0) or 0),
                        unit_price=str(getattr(li, "unit_price", 0)),
                        total_price=str(getattr(li, "total_price", 0)),
                    )
                )
        return LedgerRow(
            row_type=StatementRowType.INVOICE.value,
            reference_id=inv.id,
            reference_number=inv.invoice_number,
            issue_date=inv.issue_date,
            payment_date=None,
            order_ref=order_ref,
            status=inv.payment_status,
            amount=_signed_invoice_amount(inv),
            display_amount=_signed_invoice_amount(inv),
            line_items=line_items,
        )

    def _payment_row(self, payment: BillingPayment) -> LedgerRow:
        return LedgerRow(
            row_type=StatementRowType.PAYMENT.value,
            reference_id=payment.id,
            reference_number=payment.payment_number,
            issue_date=payment.payment_date,
            payment_date=payment.payment_date,
            order_ref=None,
            status=payment.status,
            amount=_signed_payment_amount(payment),
            display_amount=_signed_payment_amount(payment),
        )

    def _credit_note_row(self, cn: CreditNote) -> LedgerRow:
        return LedgerRow(
            row_type=StatementRowType.CREDIT_NOTE.value,
            reference_id=cn.id,
            reference_number=cn.credit_note_number,
            issue_date=cn.issue_date,
            payment_date=None,
            order_ref=None,
            status=cn.status,
            amount=_signed_credit_note_amount(cn),
            display_amount=_signed_credit_note_amount(cn),
        )

    def _refund_row(self, refund: Refund) -> LedgerRow:
        completed = refund.completed_at.date() if refund.completed_at else refund.initiated_at.date() if refund.initiated_at else date.today()
        return LedgerRow(
            row_type=StatementRowType.REFUND.value,
            reference_id=refund.id,
            reference_number=refund.refund_number,
            issue_date=completed,
            payment_date=completed,
            order_ref=refund.linked_booking_ref,
            status=refund.status,
            amount=_signed_refund_amount(refund),
            display_amount=_signed_refund_amount(refund),
        )
