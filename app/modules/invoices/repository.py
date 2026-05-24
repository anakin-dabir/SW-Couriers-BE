"""Repositories for invoices, events, PDF artifacts, and credit notes.

- InvoiceRepository: CRUD, next_invoice_number (INV-NNNNNN), list with filters, get_with_relations.
- InvoiceEventRepository: append-only event log (CREATED, FINALIZED, VOIDED, etc.).
- InvoicePdfArtifactRepository: PDF artifacts by signature (dedupe), version, latest for polling.
- CreditNoteRepository: credit notes, next_credit_note_number (CN-NNNNNN).
- InvoiceCreditApplicationRepository: applications per invoice, applied total for balance.
All list/filter methods respect organization_id for B2B scoping when provided.
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Text, and_, case, exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.exceptions import ValidationError
from app.common.repository import BaseRepository

from app.modules.billing.models import BillingPayment, BillingPaymentAllocation, Refund
from app.modules.invoices.enums import InvoiceStatus
from app.modules.invoices.models import (
    CreditNotePdfArtifact,
    CreditNote,
    Invoice,
    InvoiceCreditApplication,
    InvoiceEvent,
    InvoicePdfArtifact,
)
from app.modules.orders.models import Order


_PORTAL_PAYMENT_FILTER_VALUES = frozenset(
    {
        "UNPAID",
        "PARTIALLY_PAID",
        "PAID",
        "OVERDUE",
        "VOID",
        "WRITTEN_OFF",
        "REFUNDED",
        "DISPUTED",
    }
)


def _portal_payment_status_clause(payment_status: list[str], payment_status_expr):
    """Combine stored payment_status / overdue derivation with portal-only REFUNDED and DISPUTED exists filters."""
    invalid = [p for p in payment_status if p not in _PORTAL_PAYMENT_FILTER_VALUES]
    if invalid:
        raise ValidationError(f"payment_status must be one of {sorted(_PORTAL_PAYMENT_FILTER_VALUES)}")
    special = set(payment_status) & {"REFUNDED", "DISPUTED"}
    standard = [p for p in payment_status if p not in special]
    clauses = []
    if standard:
        clauses.append(payment_status_expr.in_(standard))
    if "REFUNDED" in special:
        clauses.append(
            exists(
                select(1)
                .select_from(Refund)
                .where(
                    Refund.invoice_id == Invoice.id,
                    Refund.status == "COMPLETED",
                    Refund.processed_amount > 0,
                )
            )
        )
    if "DISPUTED" in special:
        clauses.append(
            exists(
                select(1)
                .select_from(BillingPaymentAllocation)
                .join(BillingPayment, BillingPayment.id == BillingPaymentAllocation.payment_id)
                .where(
                    BillingPaymentAllocation.invoice_id == Invoice.id,
                    BillingPayment.braintree_status.ilike("%dispute%"),
                )
            )
        )
    if not clauses:
        raise ValidationError("payment_status must not be empty")
    return or_(*clauses) if len(clauses) > 1 else clauses[0]


class InvoiceRepository(BaseRepository):
    """Data access for Invoice records."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Invoice)

    async def next_invoice_number(self) -> str:
        """Get next INV number using DB sequence when available; fallback for pre-migration DBs."""
        try:
            result = await self.session.execute(text("SELECT nextval('invoice_number_seq')"))
            num = int(result.scalar_one())
            return f"INV-{num:06d}"
        except Exception:
            stmt = select(func.max(Invoice.invoice_number)).where(Invoice.invoice_number.like("INV-%"))
            result = await self.session.execute(stmt)
            max_code: str | None = result.scalar_one_or_none()
            if not max_code:
                return "INV-000001"
            try:
                _, num_str = max_code.split("-", 1)
                num = int(num_str) + 1
            except (ValueError, AttributeError):
                num = 1
            return f"INV-{num:06d}"  # INV-000001 .. INV-999999, then INV-1000000, ...

    async def get_by_id_with_order(
        self,
        id: str,
        organization_id: str | None = None,
        customer_id: str | None = None,
    ) -> Invoice | None:
        """Get invoice by id with order and line_items loaded. Optional org/customer scope."""
        stmt = select(Invoice).where(Invoice.id == id).options(selectinload(Invoice.order), selectinload(Invoice.line_items))
        if organization_id is not None:
            stmt = stmt.where(Invoice.organization_id == organization_id)
        if customer_id is not None:
            stmt = stmt.where(Invoice.customer_id == customer_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_order_id(self, order_id: str, organization_id: str | None = None) -> Invoice | None:
        """Get invoice by order id with line items loaded. Optional org scope."""
        stmt = (
            select(Invoice)
            .where(Invoice.order_id == order_id)
            .options(selectinload(Invoice.order), selectinload(Invoice.line_items))
            .order_by(Invoice.created_at.desc())
            .limit(1)
        )
        if organization_id is not None:
            stmt = stmt.where(Invoice.organization_id == organization_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_invoices(
        self,
        *,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        status: list[str] | None = None,
        payment_status: list[str] | None = None,
        show_draft: bool = False,
        invoiced_from: date | None = None,
        invoiced_to: date | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        period: str | None = None,
        organization_id: str | None = None,
        customer_id: str | None = None,
        sort_by: str = "issue_date",
        sort_order: str = "desc",
    ) -> tuple[list[Invoice], int]:

        """Paginated list with search (invoice_number, order reference), invoice/payment status, and date filters."""

        # Resolve period to date range (invoiced date)
        if period == "last_7_days":
            end = date.today()
            start = end - timedelta(days=7)
            if invoiced_from is None:
                invoiced_from = start
            if invoiced_to is None:
                invoiced_to = end
        elif period == "last_30_days":
            end = date.today()
            start = end - timedelta(days=30)
            if invoiced_from is None:
                invoiced_from = start
            if invoiced_to is None:
                invoiced_to = end

        stmt = select(Invoice).join(Order, Invoice.order_id == Order.id, isouter=True).options(selectinload(Invoice.order))
        count_stmt = select(func.count()).select_from(Invoice).join(Order, Invoice.order_id == Order.id, isouter=True)

        if organization_id is not None:
            stmt = stmt.where(Invoice.organization_id == organization_id)
            count_stmt = count_stmt.where(Invoice.organization_id == organization_id)
        if customer_id is not None:
            stmt = stmt.where(Invoice.customer_id == customer_id)
            count_stmt = count_stmt.where(Invoice.customer_id == customer_id)

        if search:
            pattern = f"%{search.strip()}%"
            search_filter = or_(
                Invoice.invoice_number.ilike(pattern),
                Order.order_id.ilike(pattern),
            )
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        if status:
            valid_statuses = {InvoiceStatus.DRAFT, InvoiceStatus.SENT}
            invalid = [s for s in status if s not in valid_statuses]
            if invalid:
                raise ValidationError(f"status must be one of {sorted(valid_statuses)}")
            stmt = stmt.where(Invoice.status.in_(status))
            count_stmt = count_stmt.where(Invoice.status.in_(status))
        elif not show_draft:
            stmt = stmt.where(Invoice.status != InvoiceStatus.DRAFT)
            count_stmt = count_stmt.where(Invoice.status != InvoiceStatus.DRAFT)

        payment_status_expr = case(
            (and_(Invoice.payment_status == "UNPAID", Invoice.due_date < date.today()), "OVERDUE"),
            else_=Invoice.payment_status,
        )
        if payment_status:
            ps_clause = _portal_payment_status_clause(payment_status, payment_status_expr)
            stmt = stmt.where(ps_clause)
            count_stmt = count_stmt.where(ps_clause)

        if invoiced_from is not None:
            stmt = stmt.where(Invoice.issue_date >= invoiced_from)
            count_stmt = count_stmt.where(Invoice.issue_date >= invoiced_from)
        if invoiced_to is not None:
            stmt = stmt.where(Invoice.issue_date <= invoiced_to)
            count_stmt = count_stmt.where(Invoice.issue_date <= invoiced_to)
        if due_from is not None:
            stmt = stmt.where(Invoice.due_date >= due_from)
            count_stmt = count_stmt.where(Invoice.due_date >= due_from)
        if due_to is not None:
            stmt = stmt.where(Invoice.due_date <= due_to)
            count_stmt = count_stmt.where(Invoice.due_date <= due_to)

        count_result = await self.session.execute(count_stmt)
        total: int = count_result.scalar_one()

        offset = (page - 1) * size
        sort_field = (sort_by or "issue_date").strip().lower()
        sort_dir_desc = (sort_order or "desc").strip().lower() != "asc"
        credit_applied_subq = (
            select(func.coalesce(func.sum(InvoiceCreditApplication.applied_amount), 0))
            .where(InvoiceCreditApplication.invoice_id == Invoice.id)
            .correlate(Invoice)
            .scalar_subquery()
        )
        outstanding_balance_expr = Invoice.total - credit_applied_subq - Invoice.paid_amount
        sort_map = {
            "issue_date": Invoice.issue_date,
            "due_date": Invoice.due_date,
            "total": Invoice.total,
            "paid": Invoice.paid_amount,
            "balance": outstanding_balance_expr,
            "invoice_number": Invoice.invoice_number,
        }
        sort_col = sort_map.get(sort_field, Invoice.issue_date)
        ordered_col = sort_col.desc() if sort_dir_desc else sort_col.asc()
        stmt = stmt.order_by(ordered_col, Invoice.id.desc()).offset(offset).limit(size)
        result = await self.session.execute(stmt)
        items = list(result.unique().scalars().all())

        return items, total

    async def summary_invoices(
        self,
        *,
        search: str | None = None,
        status: list[str] | None = None,
        payment_status: list[str] | None = None,
        show_draft: bool = False,
        invoiced_from: date | None = None,
        invoiced_to: date | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        period: str | None = None,
        organization_id: str | None = None,
        customer_id: str | None = None,
    ) -> dict[str, int]:
        """Aggregate invoice KPI counts with same filters as list endpoint."""
        if period == "last_7_days":
            end = date.today()
            start = end - timedelta(days=7)
            if invoiced_from is None:
                invoiced_from = start
            if invoiced_to is None:
                invoiced_to = end
        elif period == "last_30_days":
            end = date.today()
            start = end - timedelta(days=30)
            if invoiced_from is None:
                invoiced_from = start
            if invoiced_to is None:
                invoiced_to = end

        payment_status_expr = case(
            (and_(Invoice.payment_status == "UNPAID", Invoice.due_date < date.today()), "OVERDUE"),
            else_=Invoice.payment_status,
        )

        base_stmt = select(Invoice.id, payment_status_expr.label("effective_payment_status")).join(
            Order, Invoice.order_id == Order.id, isouter=True
        )

        if organization_id is not None:
            base_stmt = base_stmt.where(Invoice.organization_id == organization_id)
        if customer_id is not None:
            base_stmt = base_stmt.where(Invoice.customer_id == customer_id)

        if search:
            pattern = f"%{search.strip()}%"
            search_filter = or_(
                Invoice.invoice_number.ilike(pattern),
                Order.order_id.ilike(pattern),
            )
            base_stmt = base_stmt.where(search_filter)

        if status:
            valid_statuses = {InvoiceStatus.DRAFT, InvoiceStatus.SENT}
            invalid = [s for s in status if s not in valid_statuses]
            if invalid:
                raise ValidationError(f"status must be one of {sorted(valid_statuses)}")
            base_stmt = base_stmt.where(Invoice.status.in_(status))
        elif not show_draft:
            base_stmt = base_stmt.where(Invoice.status != InvoiceStatus.DRAFT)

        if payment_status:
            base_stmt = base_stmt.where(_portal_payment_status_clause(payment_status, payment_status_expr))

        if invoiced_from is not None:
            base_stmt = base_stmt.where(Invoice.issue_date >= invoiced_from)
        if invoiced_to is not None:
            base_stmt = base_stmt.where(Invoice.issue_date <= invoiced_to)
        if due_from is not None:
            base_stmt = base_stmt.where(Invoice.due_date >= due_from)
        if due_to is not None:
            base_stmt = base_stmt.where(Invoice.due_date <= due_to)

        subq = base_stmt.subquery()
        stmt = select(
            func.count().label("total_invoices"),
            func.coalesce(func.sum(case((subq.c.effective_payment_status == "PAID", 1), else_=0)), 0).label("total_paid"),
            func.coalesce(
                func.sum(case((subq.c.effective_payment_status.in_(["UNPAID", "PARTIALLY_PAID"]), 1), else_=0)),
                0,
            ).label("total_unpaid"),
            func.coalesce(func.sum(case((subq.c.effective_payment_status == "OVERDUE", 1), else_=0)), 0).label("overdue"),
        )
        row = (await self.session.execute(stmt)).one()

        refund_cnt_stmt = select(func.count()).select_from(subq).where(
            exists(
                select(1)
                .select_from(Refund)
                .where(
                    Refund.invoice_id == subq.c.id,
                    Refund.status == "COMPLETED",
                    Refund.processed_amount > 0,
                )
            )
        )
        dispute_cnt_stmt = select(func.count()).select_from(subq).where(
            exists(
                select(1)
                .select_from(BillingPaymentAllocation)
                .join(BillingPayment, BillingPayment.id == BillingPaymentAllocation.payment_id)
                .where(
                    BillingPaymentAllocation.invoice_id == subq.c.id,
                    BillingPayment.braintree_status.ilike("%dispute%"),
                )
            )
        )
        with_completed_refunds = int((await self.session.execute(refund_cnt_stmt)).scalar_one() or 0)
        with_open_disputes = int((await self.session.execute(dispute_cnt_stmt)).scalar_one() or 0)

        return {
            "total_invoices": int(row.total_invoices or 0),
            "total_paid": int(row.total_paid or 0),
            "total_unpaid": int(row.total_unpaid or 0),
            "overdue": int(row.overdue or 0),
            "with_completed_refunds": with_completed_refunds,
            "with_open_disputes": with_open_disputes,
        }

    async def get_with_relations(
        self,
        id: str,
        organization_id: str | None = None,
        customer_id: str | None = None,
    ) -> Invoice | None:
        """Get invoice with relations for detail view. Optional org/customer scope."""
        stmt = (
            select(Invoice)
            .where(Invoice.id == id)
            .options(
                selectinload(Invoice.order),
                selectinload(Invoice.organization),
                selectinload(Invoice.line_items),
                selectinload(Invoice.events),
                selectinload(Invoice.credit_applications).selectinload(InvoiceCreditApplication.credit_note),
            )
        )
        if organization_id is not None:
            stmt = stmt.where(Invoice.organization_id == organization_id)
        if customer_id is not None:
            stmt = stmt.where(Invoice.customer_id == customer_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_by_order_id(self, order_id: str) -> bool:
        """Check if an invoice already exists for this order (one invoice per order)."""
        return await self.exists(order_id=order_id)


class InvoiceEventRepository(BaseRepository):
    """Append-only invoice activity events."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, InvoiceEvent)

    async def append(
        self,
        invoice_id: str,
        event_type: str,
        *,
        reason: str | None = None,
        actor_id: str | None = None,
        actor_role: str | None = None,
    ) -> InvoiceEvent:
        """Append an invoice event. Never raises."""
        entry = InvoiceEvent(
            invoice_id=invoice_id,
            event_type=event_type,
            reason=reason,
            actor_id=actor_id,
            actor_role=actor_role,
        )
        self.session.add(entry)
        await self.session.flush()
        await self.session.refresh(entry)
        return entry

    async def latest_outcome_event_type(self, invoice_id: str) -> str | None:
        """Latest explicit outcome event (VOIDED/WRITTEN_OFF), if any."""
        stmt = (
            select(InvoiceEvent.event_type)
            .where(
                InvoiceEvent.invoice_id == invoice_id,
                InvoiceEvent.event_type.in_(["VOIDED", "WRITTEN_OFF"]),
            )
            .order_by(InvoiceEvent.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def latest_void_written_off_for_invoice_ids(self, invoice_ids: list[str]) -> dict[str, str]:
        """Latest VOIDED or WRITTEN_OFF event per invoice (by ``created_at``), if any."""
        if not invoice_ids:
            return {}
        stmt = (
            select(InvoiceEvent.invoice_id, InvoiceEvent.event_type)
            .where(
                InvoiceEvent.invoice_id.in_(invoice_ids),
                InvoiceEvent.event_type.in_(["VOIDED", "WRITTEN_OFF"]),
            )
            .order_by(InvoiceEvent.created_at.desc())
        )
        result = await self.session.execute(stmt)
        out: dict[str, str] = {}
        for iid, etype in result:
            key = str(iid)
            if key not in out:
                out[key] = str(etype)
        return out


class InvoicePdfArtifactRepository(BaseRepository):
    """PDF artifacts per (invoice, template_version, signature_hash)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, InvoicePdfArtifact)

    async def get_ready_by_signature(
        self,
        invoice_id: str,
        template_version: str,
        signature_hash: str,
    ) -> InvoicePdfArtifact | None:
        """Find a READY artifact for this invoice + template + signature (for dedupe)."""
        stmt = select(InvoicePdfArtifact).where(
            InvoicePdfArtifact.invoice_id == invoice_id,
            InvoicePdfArtifact.template_version == template_version,
            InvoicePdfArtifact.signature_hash == signature_hash,
            InvoicePdfArtifact.status == "READY",
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_generating_by_signature(
        self,
        invoice_id: str,
        template_version: str,
        signature_hash: str,
    ) -> InvoicePdfArtifact | None:
        """Find latest in-flight GENERATING artifact for this invoice + template + signature."""
        stmt = (
            select(InvoicePdfArtifact)
            .where(
                InvoicePdfArtifact.invoice_id == invoice_id,
                InvoicePdfArtifact.template_version == template_version,
                InvoicePdfArtifact.signature_hash == signature_hash,
                InvoicePdfArtifact.status == "GENERATING",
            )
            .order_by(InvoicePdfArtifact.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_next_pdf_version(self, invoice_id: str) -> int:
        """Next pdf_version (monotonic) for this invoice."""
        stmt = select(func.coalesce(func.max(InvoicePdfArtifact.pdf_version), 0)).where(InvoicePdfArtifact.invoice_id == invoice_id)
        result = await self.session.execute(stmt)
        return int(result.scalar_one()) + 1

    async def get_latest_for_invoice(self, invoice_id: str) -> InvoicePdfArtifact | None:
        """Latest artifact (by created_at) for polling/signed-url."""
        stmt = select(InvoicePdfArtifact).where(InvoicePdfArtifact.invoice_id == invoice_id).order_by(InvoicePdfArtifact.created_at.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class CreditNoteRepository(BaseRepository):
    """Data access for CreditNote (credit memo) records."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, CreditNote)

    async def next_credit_note_number(self) -> str:
        """Generate next credit note number (e.g. CN-000001)."""
        stmt = select(func.max(CreditNote.credit_note_number)).where(CreditNote.credit_note_number.like("CN-%"))
        result = await self.session.execute(stmt)
        max_code: str | None = result.scalar_one_or_none()
        if not max_code:
            return "CN-000001"
        try:
            _, num_str = max_code.split("-", 1)
            num = int(num_str) + 1
        except (ValueError, AttributeError):
            num = 1
        return f"CN-{num:06d}"

    async def get_with_relations(self, credit_note_id: str, *, organization_id: str | None = None) -> CreditNote | None:
        stmt = (
            select(CreditNote)
            .where(CreditNote.id == credit_note_id)
            .options(
                selectinload(CreditNote.source_invoice),
                selectinload(CreditNote.applications)
                .selectinload(InvoiceCreditApplication.invoice)
                .selectinload(Invoice.order),
            )
        )
        if organization_id is not None:
            stmt = stmt.where(CreditNote.organization_id == organization_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_credit_notes(
        self,
        *,
        page: int = 1,
        size: int = 20,
        organization_id: str | None = None,
        customer_id: str | None = None,
        customer_unassigned_only: bool = False,
        search: str | None = None,
        status: list[str] | None = None,
        reason_category: list[str] | None = None,
        issued_from: date | None = None,
        issued_to: date | None = None,
        sort_by: str = "issue_date",
        sort_order: str = "desc",
    ) -> tuple[list[CreditNote], int]:
        applied_subq = (
            select(
                InvoiceCreditApplication.credit_note_id.label("credit_note_id"),
                func.coalesce(func.sum(InvoiceCreditApplication.applied_amount), 0).label("applied_total"),
            )
            .group_by(InvoiceCreditApplication.credit_note_id)
            .subquery()
        )
        stmt = (
            select(CreditNote, applied_subq.c.applied_total)
            .outerjoin(applied_subq, applied_subq.c.credit_note_id == CreditNote.id)
            .outerjoin(Invoice, Invoice.id == CreditNote.source_invoice_id)
            .outerjoin(Order, Order.id == Invoice.order_id)
        )
        if organization_id is not None:
            stmt = stmt.where(CreditNote.organization_id == organization_id)
        if customer_unassigned_only:
            stmt = stmt.where(CreditNote.customer_id.is_(None))
        elif customer_id is not None:
            stmt = stmt.where(CreditNote.customer_id == customer_id)
        if search:
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    CreditNote.credit_note_number.ilike(pattern),
                    func.cast(CreditNote.total_credit_amount, Text).ilike(pattern),
                    Invoice.invoice_number.ilike(pattern),
                    Order.order_id.ilike(pattern),
                )
            )
        if status:
            normalized = {s.strip().upper() for s in status if s and s.strip()}
            allowed = {"OPEN", "VOID", "FULLY_APPLIED", "PARTIALLY_APPLIED"}
            invalid = sorted(normalized - allowed)
            if invalid:
                raise ValidationError(f"status must be one of {sorted(allowed)}")
            conds = []
            applied = func.coalesce(applied_subq.c.applied_total, 0)
            remaining = CreditNote.total_credit_amount - applied
            if "OPEN" in normalized:
                conds.append(and_(CreditNote.status == "ISSUED", applied <= 0))
            if "VOID" in normalized:
                conds.append(CreditNote.status.in_(["VOIDED", "WRITTEN_OFF"]))
            if "FULLY_APPLIED" in normalized:
                conds.append(and_(CreditNote.status == "ISSUED", applied > 0, remaining <= 0))
            if "PARTIALLY_APPLIED" in normalized:
                conds.append(and_(CreditNote.status == "ISSUED", applied > 0, remaining > 0))
            if conds:
                stmt = stmt.where(or_(*conds))
        if reason_category:
            stmt = stmt.where(CreditNote.reason_category.in_(reason_category))
        if issued_from is not None:
            stmt = stmt.where(CreditNote.issue_date >= issued_from)
        if issued_to is not None:
            stmt = stmt.where(CreditNote.issue_date <= issued_to)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self.session.execute(count_stmt)).scalar_one() or 0)
        sort_map = {
            "issue_date": CreditNote.issue_date,
            "amount": CreditNote.total_credit_amount,
            "credit_note_number": CreditNote.credit_note_number,
        }
        sort_col = sort_map.get((sort_by or "").strip().lower(), CreditNote.issue_date)
        ordered_col = sort_col.asc() if (sort_order or "").strip().lower() == "asc" else sort_col.desc()
        rows = (
            await self.session.execute(
                stmt.order_by(ordered_col, CreditNote.id.desc()).offset((page - 1) * size).limit(size)
            )
        ).all()
        items: list[CreditNote] = []
        for cn, applied_total in rows:
            setattr(cn, "_applied_total", applied_total or Decimal("0"))
            items.append(cn)
        return items, total


class InvoiceCreditApplicationRepository(BaseRepository):
    """Applications of credit notes to invoices."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, InvoiceCreditApplication)

    async def get_applied_total_for_invoice(self, invoice_id: str) -> Decimal:
        """Sum of applied_amount for this invoice (for total_after_credit)."""
        stmt = select(func.coalesce(func.sum(InvoiceCreditApplication.applied_amount), 0)).where(InvoiceCreditApplication.invoice_id == invoice_id)
        result = await self.session.execute(stmt)
        return result.scalar_one() or Decimal("0")

    async def totals_applied_for_invoices(self, invoice_ids: list[str]) -> dict[str, Decimal]:
        """Sum credit-note applications per invoice (balance math aligned with allocation candidates)."""
        if not invoice_ids:
            return {}
        stmt = (
            select(InvoiceCreditApplication.invoice_id, func.coalesce(func.sum(InvoiceCreditApplication.applied_amount), 0))
            .where(InvoiceCreditApplication.invoice_id.in_(invoice_ids))
            .group_by(InvoiceCreditApplication.invoice_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {str(iid): (amt or Decimal("0")) for iid, amt in rows}

    async def list_for_invoice(self, invoice_id: str) -> list[InvoiceCreditApplication]:
        """All credit applications for an invoice (for PDF and detail)."""
        stmt = (
            select(InvoiceCreditApplication)
            .where(InvoiceCreditApplication.invoice_id == invoice_id)
            .options(selectinload(InvoiceCreditApplication.credit_note))
            .order_by(InvoiceCreditApplication.applied_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_applied_total_for_credit_note(self, credit_note_id: str) -> Decimal:
        stmt = select(func.coalesce(func.sum(InvoiceCreditApplication.applied_amount), 0)).where(
            InvoiceCreditApplication.credit_note_id == credit_note_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one() or Decimal("0")

    async def list_for_credit_note(self, credit_note_id: str) -> list[InvoiceCreditApplication]:
        stmt = (
            select(InvoiceCreditApplication)
            .where(InvoiceCreditApplication.credit_note_id == credit_note_id)
            .options(
                selectinload(InvoiceCreditApplication.invoice).selectinload(Invoice.order),
            )
            .order_by(InvoiceCreditApplication.applied_at.desc(), InvoiceCreditApplication.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class CreditNotePdfArtifactRepository(BaseRepository):
    """PDF artifacts per (credit_note, template_version, signature_hash)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, CreditNotePdfArtifact)

    async def get_ready_by_signature(
        self,
        credit_note_id: str,
        template_version: str,
        signature_hash: str,
    ) -> CreditNotePdfArtifact | None:
        stmt = select(CreditNotePdfArtifact).where(
            CreditNotePdfArtifact.credit_note_id == credit_note_id,
            CreditNotePdfArtifact.template_version == template_version,
            CreditNotePdfArtifact.signature_hash == signature_hash,
            CreditNotePdfArtifact.status == "READY",
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_generating_by_signature(
        self,
        credit_note_id: str,
        template_version: str,
        signature_hash: str,
    ) -> CreditNotePdfArtifact | None:
        stmt = (
            select(CreditNotePdfArtifact)
            .where(
                CreditNotePdfArtifact.credit_note_id == credit_note_id,
                CreditNotePdfArtifact.template_version == template_version,
                CreditNotePdfArtifact.signature_hash == signature_hash,
                CreditNotePdfArtifact.status == "GENERATING",
            )
            .order_by(CreditNotePdfArtifact.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_next_pdf_version(self, credit_note_id: str) -> int:
        stmt = select(func.coalesce(func.max(CreditNotePdfArtifact.pdf_version), 0)).where(
            CreditNotePdfArtifact.credit_note_id == credit_note_id
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one()) + 1

    async def get_latest_for_credit_note(self, credit_note_id: str) -> CreditNotePdfArtifact | None:
        stmt = (
            select(CreditNotePdfArtifact)
            .where(CreditNotePdfArtifact.credit_note_id == credit_note_id)
            .order_by(CreditNotePdfArtifact.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
