"""Repositories for billing payments, refunds, and allocations."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Text, and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.repository import BaseRepository
from app.modules.billing.models import BillingPayment, BillingPaymentAllocation, BillingPaymentEvent, Refund, RefundEvent
from app.modules.invoices.models import Invoice


class BillingPaymentRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, BillingPayment)

    async def get_by_payment_number(self, payment_number: str, *, organization_id: str) -> BillingPayment | None:
        return await self.find_one(payment_number=payment_number, organization_id=organization_id)

    async def list_history(
        self,
        *,
        organization_id: str | None,
        page: int,
        size: int,
        search: str | None = None,
        status: list[str] | None = None,
        allocation_status: list[str] | None = None,
        provider: list[str] | None = None,
        payment_date_from: date | None = None,
        payment_date_to: date | None = None,
    ) -> tuple[list[BillingPayment], int]:
        stmt = select(BillingPayment)
        count_stmt = select(func.count()).select_from(BillingPayment)
        if organization_id is not None:
            stmt = stmt.where(BillingPayment.organization_id == organization_id)
            count_stmt = count_stmt.where(BillingPayment.organization_id == organization_id)

        if search:
            pattern = f"%{search.strip()}%"
            invoice_search = exists(
                select(1)
                .select_from(BillingPaymentAllocation)
                .join(Invoice, Invoice.id == BillingPaymentAllocation.invoice_id)
                .where(
                    BillingPaymentAllocation.payment_id == BillingPayment.id,
                    or_(
                        Invoice.invoice_number.ilike(pattern),
                        BillingPaymentAllocation.invoice_id.cast(Text).ilike(pattern),
                    ),
                )
            )
            search_filter = or_(BillingPayment.payment_number.ilike(pattern), BillingPayment.provider_txn_id.ilike(pattern), invoice_search)
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)
        if status:
            stmt = stmt.where(BillingPayment.status.in_(status))
            count_stmt = count_stmt.where(BillingPayment.status.in_(status))
        else:
            stmt = stmt.where(BillingPayment.status != "VOIDED")
            count_stmt = count_stmt.where(BillingPayment.status != "VOIDED")
        if allocation_status:
            stmt = stmt.where(BillingPayment.allocation_status.in_(allocation_status))
            count_stmt = count_stmt.where(BillingPayment.allocation_status.in_(allocation_status))
        if provider:
            stmt = stmt.where(BillingPayment.provider.in_(provider))
            count_stmt = count_stmt.where(BillingPayment.provider.in_(provider))
        if payment_date_from is not None:
            stmt = stmt.where(BillingPayment.payment_date >= payment_date_from)
            count_stmt = count_stmt.where(BillingPayment.payment_date >= payment_date_from)
        if payment_date_to is not None:
            stmt = stmt.where(BillingPayment.payment_date <= payment_date_to)
            count_stmt = count_stmt.where(BillingPayment.payment_date <= payment_date_to)

        total = int((await self.session.execute(count_stmt)).scalar_one())
        offset = (page - 1) * size
        stmt = stmt.order_by(BillingPayment.payment_date.desc(), BillingPayment.id.desc()).offset(offset).limit(size)
        items = list((await self.session.execute(stmt)).scalars().all())
        return items, total

    async def payment_kpis(
        self,
        *,
        organization_id: str | None,
        search: str | None = None,
        status: list[str] | None = None,
        allocation_status: list[str] | None = None,
        provider: list[str] | None = None,
        payment_date_from: date | None = None,
        payment_date_to: date | None = None,
    ) -> dict[str, Decimal]:
        filters = [BillingPayment.status != "VOIDED"]
        if organization_id is not None:
            filters.append(BillingPayment.organization_id == organization_id)
        if search:
            pattern = f"%{search.strip()}%"
            invoice_search = exists(
                select(1)
                .select_from(BillingPaymentAllocation)
                .join(Invoice, Invoice.id == BillingPaymentAllocation.invoice_id)
                .where(
                    BillingPaymentAllocation.payment_id == BillingPayment.id,
                    or_(
                        Invoice.invoice_number.ilike(pattern),
                        BillingPaymentAllocation.invoice_id.cast(Text).ilike(pattern),
                    ),
                )
            )
            filters.append(
                or_(
                    BillingPayment.payment_number.ilike(pattern),
                    BillingPayment.provider_txn_id.ilike(pattern),
                    invoice_search,
                )
            )
        if status:
            filters.append(BillingPayment.status.in_(status))
        if allocation_status:
            filters.append(BillingPayment.allocation_status.in_(allocation_status))
        if provider:
            filters.append(BillingPayment.provider.in_(provider))
        if payment_date_from is not None:
            filters.append(BillingPayment.payment_date >= payment_date_from)
        if payment_date_to is not None:
            filters.append(BillingPayment.payment_date <= payment_date_to)

        stmt = select(
            func.coalesce(func.sum(BillingPayment.amount), 0).label("total_received"),
            func.coalesce(func.sum(BillingPayment.allocated_amount), 0).label("allocated"),
            func.coalesce(func.sum(BillingPayment.unallocated_amount), 0).label("unallocated"),
            func.coalesce(
                func.sum(
                    case(
                        (BillingPayment.status == "PENDING", BillingPayment.amount),
                        else_=0,
                    )
                ),
                0,
            ).label("pending"),
        ).where(*filters)
        row = (await self.session.execute(stmt)).one()
        return {
            "total_received": row.total_received or Decimal("0"),
            "allocated": row.allocated or Decimal("0"),
            "unallocated": row.unallocated or Decimal("0"),
            "pending": row.pending or Decimal("0"),
        }

    async def list_for_invoice(
        self,
        *,
        invoice_id: str,
        page: int = 1,
        size: int = 20,
        organization_id: str | None = None,
    ) -> tuple[list[dict[str, object]], int]:
        """List payment rows allocated to a specific invoice."""
        alloc_subq = (
            select(
                BillingPaymentAllocation.payment_id.label("payment_id"),
                func.sum(BillingPaymentAllocation.allocated_amount).label("allocated_amount"),
            )
            .where(BillingPaymentAllocation.invoice_id == invoice_id)
            .group_by(BillingPaymentAllocation.payment_id)
            .subquery()
        )

        stmt = (
            select(
                BillingPayment.id,
                BillingPayment.payment_number,
                BillingPayment.payment_date,
                BillingPayment.provider,
                BillingPayment.provider_txn_id,
                BillingPayment.status,
                alloc_subq.c.allocated_amount,
            )
            .join(alloc_subq, alloc_subq.c.payment_id == BillingPayment.id)
        )
        if organization_id is not None:
            stmt = stmt.where(BillingPayment.organization_id == organization_id)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self.session.execute(count_stmt)).scalar_one())
        offset = (page - 1) * size
        rows = (
            await self.session.execute(
                stmt.order_by(BillingPayment.payment_date.desc(), BillingPayment.id.desc()).offset(offset).limit(size)
            )
        ).all()
        items: list[dict[str, object]] = []
        for row in rows:
            items.append(
                {
                    "payment_id": row.id,
                    "payment_number": row.payment_number,
                    "payment_date": row.payment_date,
                    "provider": row.provider,
                    "provider_txn_id": row.provider_txn_id,
                    "status": row.status,
                    "allocated_amount": row.allocated_amount or Decimal("0"),
                }
            )
        return items, total

    async def latest_method_for_invoice(
        self,
        *,
        invoice_id: str,
        organization_id: str | None = None,
    ) -> str | None:
        """Most recent payment provider mapped to invoice via allocations."""
        stmt = (
            select(BillingPayment.provider)
            .join(BillingPaymentAllocation, BillingPaymentAllocation.payment_id == BillingPayment.id)
            .where(BillingPaymentAllocation.invoice_id == invoice_id)
            .order_by(BillingPayment.payment_date.desc(), BillingPayment.created_at.desc())
            .limit(1)
        )
        if organization_id is not None:
            stmt = stmt.where(BillingPayment.organization_id == organization_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def invoice_ids_with_open_payment_dispute(self, invoice_ids: list[str]) -> set[str]:
        """Invoice IDs that have an allocated payment with an active dispute status."""
        if not invoice_ids:
            return set()
        stmt = (
            select(BillingPaymentAllocation.invoice_id)
            .distinct()
            .join(BillingPayment, BillingPayment.id == BillingPaymentAllocation.payment_id)
            .where(
                BillingPaymentAllocation.invoice_id.in_(invoice_ids),
                BillingPayment.dispute_status.isnot(None),
                BillingPayment.dispute_status.notin_(["WON", "EXPIRED"]),
            )
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return {str(i) for i in rows if i}


class BillingPaymentAllocationRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, BillingPaymentAllocation)

    async def next_revision_no(self, *, payment_id: str, invoice_id: str) -> int:
        stmt = select(func.coalesce(func.max(BillingPaymentAllocation.revision_no), 0)).where(
            BillingPaymentAllocation.payment_id == payment_id,
            BillingPaymentAllocation.invoice_id == invoice_id,
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one()) + 1

    async def latest_for_payment(self, payment_id: str) -> list[BillingPaymentAllocation]:
        # Immutable-additive allocations: return one row per invoice with cumulative allocated amount.
        agg_subq = (
            select(
                BillingPaymentAllocation.invoice_id,
                func.sum(BillingPaymentAllocation.allocated_amount).label("allocated_amount"),
                func.max(BillingPaymentAllocation.revision_no).label("revision_no"),
                func.max(BillingPaymentAllocation.created_at).label("created_at"),
            )
            .where(BillingPaymentAllocation.payment_id == payment_id)
            .group_by(BillingPaymentAllocation.invoice_id)
            .having(func.sum(BillingPaymentAllocation.allocated_amount) != 0)
            .subquery()
        )
        stmt = select(agg_subq.c.invoice_id, agg_subq.c.allocated_amount, agg_subq.c.revision_no, agg_subq.c.created_at).order_by(
            agg_subq.c.invoice_id.asc()
        )
        result = await self.session.execute(stmt)
        rows = []
        for row in result:
            rows.append(
                BillingPaymentAllocation(
                    payment_id=payment_id,
                    invoice_id=row.invoice_id,
                    allocated_amount=row.allocated_amount,
                    revision_no=row.revision_no,
                    created_at=row.created_at,
                )
            )
        return rows

    async def total_latest_allocated_for_payment(self, payment_id: str) -> Decimal:
        stmt = select(func.coalesce(func.sum(BillingPaymentAllocation.allocated_amount), 0)).where(BillingPaymentAllocation.payment_id == payment_id)
        result = await self.session.execute(stmt)
        return result.scalar_one() or Decimal("0")

    async def total_allocated_for_invoice(self, invoice_id: str) -> Decimal:
        """Sum allocated amounts for an invoice across all payments."""
        stmt = select(func.coalesce(func.sum(BillingPaymentAllocation.allocated_amount), 0)).where(BillingPaymentAllocation.invoice_id == invoice_id)
        result = await self.session.execute(stmt)
        return result.scalar_one() or Decimal("0")

    async def totals_allocated_for_invoices(self, invoice_ids: list[str]) -> dict[str, Decimal]:
        """Sum payment allocations per invoice (one aggregate row per invoice id)."""
        if not invoice_ids:
            return {}
        stmt = (
            select(BillingPaymentAllocation.invoice_id, func.coalesce(func.sum(BillingPaymentAllocation.allocated_amount), 0))
            .where(BillingPaymentAllocation.invoice_id.in_(invoice_ids))
            .group_by(BillingPaymentAllocation.invoice_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {str(iid): (amt or Decimal("0")) for iid, amt in rows}

    async def summaries_for_payments(self, payment_ids: list[str]) -> dict[str, list[dict[str, object]]]:
        if not payment_ids:
            return {}
        stmt = (
            select(
                BillingPaymentAllocation.payment_id,
                BillingPaymentAllocation.invoice_id,
                Invoice.invoice_number,
                func.sum(BillingPaymentAllocation.allocated_amount).label("allocated_amount"),
            )
            .join(Invoice, Invoice.id == BillingPaymentAllocation.invoice_id)
            .where(BillingPaymentAllocation.payment_id.in_(payment_ids))
            .group_by(
                BillingPaymentAllocation.payment_id,
                BillingPaymentAllocation.invoice_id,
                Invoice.invoice_number,
            )
            .having(func.sum(BillingPaymentAllocation.allocated_amount) != 0)
            .order_by(BillingPaymentAllocation.payment_id.asc(), BillingPaymentAllocation.invoice_id.asc())
        )
        rows = await self.session.execute(stmt)
        grouped: dict[str, list[dict[str, object]]] = {}
        for payment_id, invoice_id, invoice_number, allocated_amount in rows.all():
            grouped.setdefault(payment_id, []).append(
                {
                    "invoice_id": invoice_id,
                    "invoice_number": invoice_number,
                    "allocated_amount": allocated_amount or Decimal("0"),
                }
            )
        return grouped


class BillingPaymentEventRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, BillingPaymentEvent)


class RefundRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Refund)

    async def get_by_refund_id(self, refund_id: str) -> Refund | None:
        return await self.find_one(id=refund_id)

    async def next_refund_number(self) -> str:
        stmt = select(func.max(Refund.refund_number)).where(Refund.refund_number.like("REF-%"))
        result = await self.session.execute(stmt)
        max_code: str | None = result.scalar_one_or_none()
        if not max_code:
            return "REF-000001"
        try:
            _, num_str = max_code.split("-", 1)
            num = int(num_str) + 1
        except (ValueError, AttributeError):
            num = 1
        return f"REF-{num:06d}"

    async def total_non_reversed_refunded_for_payment(self, payment_id: str) -> Decimal:
        stmt = select(func.coalesce(func.sum(Refund.processed_amount), 0)).where(
            Refund.billing_payment_id == payment_id,
            Refund.status != "REVERSED",
        )
        result = await self.session.execute(stmt)
        return result.scalar_one() or Decimal("0")

    async def summarize_refunds_by_invoice_ids(self, invoice_ids: list[str]) -> dict[str, dict[str, object]]:
        """Per-invoice refund rollups for portal list/detail (excludes REVERSED)."""
        if not invoice_ids:
            return {}
        stmt = (
            select(
                Refund.invoice_id,
                func.coalesce(
                    func.sum(case((Refund.status == "COMPLETED", Refund.processed_amount), else_=0)),
                    0,
                ).label("refunded_amount"),
                func.coalesce(func.sum(case((Refund.status.in_(["INITIATED", "PROCESSING"]), 1), else_=0)), 0).label(
                    "pending_refund_count"
                ),
                func.coalesce(func.sum(case((Refund.status == "COMPLETED", 1), else_=0)), 0).label("completed_refund_count"),
            )
            .where(Refund.invoice_id.in_(invoice_ids), Refund.status != "REVERSED", Refund.invoice_id.isnot(None))
            .group_by(Refund.invoice_id)
        )
        out: dict[str, dict[str, object]] = {}
        for row in (await self.session.execute(stmt)).all():
            if row.invoice_id is None:
                continue
            iid = str(row.invoice_id)
            out[iid] = {
                "refunded_amount": row.refunded_amount or Decimal("0"),
                "pending_refund_count": int(row.pending_refund_count or 0),
                "completed_refund_count": int(row.completed_refund_count or 0),
            }
        return out

    async def list_refunds(
        self,
        *,
        organization_id: str,
        page: int,
        size: int,
        search: str | None = None,
        status: list[str] | None = None,
        refund_type: list[str] | None = None,
        refund_method: list[str] | None = None,
        reason_category: list[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[list[Refund], int]:
        base_stmt = (
            select(Refund, BillingPayment.payment_number, Invoice.invoice_number)
            .join(BillingPayment, BillingPayment.id == Refund.billing_payment_id)
            .outerjoin(Invoice, Invoice.id == Refund.invoice_id)
            .where(Refund.organization_id == organization_id)
        )
        stmt = base_stmt

        if search:
            pattern = f"%{search.strip()}%"
            search_filter = or_(
                Refund.refund_number.ilike(pattern),
                BillingPayment.payment_number.ilike(pattern),
                Invoice.invoice_number.ilike(pattern),
                Refund.braintree_transaction_id.ilike(pattern),
                Refund.linked_booking_ref.ilike(pattern),
            )
            stmt = stmt.where(search_filter)
        if status:
            stmt = stmt.where(Refund.status.in_(status))
        if refund_type:
            stmt = stmt.where(Refund.refund_type.in_(refund_type))
        if refund_method:
            stmt = stmt.where(Refund.refund_method.in_(refund_method))
        if reason_category:
            stmt = stmt.where(Refund.reason_category.in_(reason_category))
        if date_from is not None:
            start_dt = datetime.combine(date_from, datetime.min.time())
            stmt = stmt.where(Refund.created_at >= start_dt)
        if date_to is not None:
            end_dt = datetime.combine(date_to, datetime.max.time())
            stmt = stmt.where(Refund.created_at <= end_dt)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self.session.execute(count_stmt)).scalar_one())
        offset = (page - 1) * size
        stmt = stmt.order_by(Refund.created_at.desc(), Refund.id.desc()).offset(offset).limit(size)
        rows = (await self.session.execute(stmt)).all()
        items: list[Refund] = []
        for refund, payment_number, invoice_number in rows:
            refund._payment_number = payment_number
            refund._invoice_number = invoice_number
            items.append(refund)
        return items, total

    async def kpis(
        self,
        *,
        organization_id: str,
        status: list[str] | None = None,
        refund_type: list[str] | None = None,
        refund_method: list[str] | None = None,
        reason_category: list[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Decimal | int]:
        where = [Refund.organization_id == organization_id]
        if status:
            where.append(Refund.status.in_(status))
        if refund_type:
            where.append(Refund.refund_type.in_(refund_type))
        if refund_method:
            where.append(Refund.refund_method.in_(refund_method))
        if reason_category:
            where.append(Refund.reason_category.in_(reason_category))
        if date_from is not None:
            where.append(Refund.created_at >= datetime.combine(date_from, datetime.min.time()))
        if date_to is not None:
            where.append(Refund.created_at <= datetime.combine(date_to, datetime.max.time()))

        stmt = select(
            func.coalesce(func.sum(Refund.processed_amount), 0).label("total_refund_amount"),
            func.coalesce(func.sum(case((func.date_trunc("month", Refund.created_at) == func.date_trunc("month", func.now()), 1), else_=0)), 0).label(
                "refunds_this_month"
            ),
            func.coalesce(func.sum(case((Refund.status == "PROCESSING", 1), else_=0)), 0).label("pending_refunds"),
            func.coalesce(func.sum(case((Refund.status == "FAILED", 1), else_=0)), 0).label("failed_refunds"),
            func.coalesce(
                func.avg(
                    case(
                        (and_(Refund.status == "COMPLETED", Refund.completed_at.isnot(None), Refund.initiated_at.isnot(None)),
                         func.extract("epoch", Refund.completed_at - Refund.initiated_at) / 86400),
                        else_=None,
                    )
                ),
                0,
            ).label("avg_refund_days"),
        ).where(*where)
        row = (await self.session.execute(stmt)).one()
        return {
            "total_refund_amount": row.total_refund_amount or Decimal("0"),
            "refunds_this_month": int(row.refunds_this_month or 0),
            "pending_refunds": int(row.pending_refunds or 0),
            "failed_refunds": int(row.failed_refunds or 0),
            "avg_refund_time_days": int(round(float(row.avg_refund_days or 0))),
        }

    async def lock_payment(self, payment_id: str, organization_id: str) -> BillingPayment:
        stmt = (
            select(BillingPayment)
            .where(BillingPayment.id == payment_id, BillingPayment.organization_id == organization_id)
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            raise ValueError("payment_not_found")
        return row


class RefundEventRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, RefundEvent)
