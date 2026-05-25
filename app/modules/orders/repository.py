from __future__ import annotations

from typing import Any
from datetime import date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import Select, String, and_, case, cast, func, or_, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload

from app.common.enums.jobs import Job
from app.common.repository import BaseRepository
from app.common.validators import UUID_REGEX_PATTERN
from app.core.queue import QueuePriority, enqueue
from app.modules.orders.enums import (
    FAILED_PACKAGE_STATUSES,
    PICKUP_ON_ROUTE_ORDER_STATUSES,
    RETURN_PACKAGE_STATUSES,
    DeliveryStopStatus,
    OrderDraftStatus,
    OrderStatus,
    PackageStatus,
)
from app.modules.orders.models import (
    DeliveryStop,
    DeliveryStopEvent,
    DeliveryStopFailedAttempt,
    DeliveryStopReturnAttempt,
    DeliveryStopReturnEvidenceImage,
    Order,
    OrderDraft,
    OrderEvent,
    Package,
    PackageEvent,
    PackageMissingReport,
    PackageScanLog,
    StopNote,
    StopNoteAcknowledgement,
    StopNoteImage,
)
from app.modules.organizations.models import Organization
from app.modules.pickup_addresses.models import PickupAddress
from app.modules.user.models import User


def _draft_payload_pickup_address_id_expr():
    """Pickup id from draft JSON; supports legacy pickup_request_id key."""
    return func.coalesce(
        OrderDraft.payload["pickup_address_id"].astext,
        OrderDraft.payload["pickup_request_id"].astext,
    )


def _safe_pickup_address_join(pickup_address_id_expr):
    """Outer-join pickup_addresses only when the payload value is a valid UUID string.

    Uses CASE so PostgreSQL never casts invalid strings (AND does not short-circuit casts).
    """
    has_valid_uuid = and_(
        pickup_address_id_expr.isnot(None),
        pickup_address_id_expr != "",
        pickup_address_id_expr.op("~*")(UUID_REGEX_PATTERN),
    )
    pickup_address_uuid = case(
        (has_valid_uuid, cast(pickup_address_id_expr, PGUUID(as_uuid=False))),
        else_=None,
    )
    return PickupAddress.id == pickup_address_uuid


class OrderDraftRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__(session, OrderDraft)

    async def list_for_org(
        self,
        organization_id: str | None,
        *,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[tuple[OrderDraft, str | None, str | None]], int]:
        created_from_dt = datetime.combine(date_from, time.min) if date_from else None
        created_to_dt_exclusive = datetime.combine(date_to + timedelta(days=1), time.min) if date_to else None

        creator_user = aliased(User)
        pickup_address_id_expr = _draft_payload_pickup_address_id_expr()
        pickup_address_join = _safe_pickup_address_join(pickup_address_id_expr)
        pickup_address_expr = func.nullif(
            func.trim(
                func.concat_ws(
                    ", ",
                    PickupAddress.line_1,
                    PickupAddress.line_2,
                    PickupAddress.city,
                    PickupAddress.postcode,
                )
            ),
            "",
        )
        created_by_name_expr = func.nullif(
            func.trim(func.concat_ws(" ", creator_user.first_name, creator_user.last_name)),
            "",
        )
        created_by_expr = func.coalesce(created_by_name_expr, creator_user.email)

        def _apply_filters(query):
            if organization_id:
                query = query.where(OrderDraft.organization_id == organization_id)
            query = query.where(OrderDraft.status == OrderDraftStatus.PENDING)
            if created_from_dt:
                query = query.where(OrderDraft.created_at >= created_from_dt)
            if created_to_dt_exclusive:
                query = query.where(OrderDraft.created_at < created_to_dt_exclusive)
            if search:
                term = f"%{search.strip()}%"
                query = query.where(
                    or_(
                        OrderDraft.draft_id.ilike(term),
                        func.coalesce(pickup_address_expr, "").ilike(term),
                        func.coalesce(created_by_expr, "").ilike(term),
                    )
                )
            return query

        join_pickup = (
            select(OrderDraft.id)
            .outerjoin(PickupAddress, pickup_address_join)
            .outerjoin(creator_user, creator_user.id == OrderDraft.created_by_id)
        )
        join_pickup = _apply_filters(join_pickup)
        count_stmt = select(func.count()).select_from(join_pickup.subquery())
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(
                OrderDraft,
                pickup_address_expr.label("pickup_address"),
                created_by_expr.label("created_by"),
            )
            .outerjoin(PickupAddress, pickup_address_join)
            .outerjoin(creator_user, creator_user.id == OrderDraft.created_by_id)
        )
        stmt = _apply_filters(stmt)
        stmt = stmt.order_by(OrderDraft.updated_at.desc()).offset(offset).limit(limit)
        rows = [(draft, pickup_address, created_by) for draft, pickup_address, created_by in (await self.session.execute(stmt)).all()]
        return rows, total


class OrderRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Order)

    async def get_by_master_label(self, master_label_id: str) -> Order | None:
        return await self.find_one(master_label_id=master_label_id)

    async def get_order_with_stops_and_packages(
        self,
        order_id: str,
    ) -> tuple[Order | None, list[DeliveryStop], dict[str, list[Package]]]:
        stmt = (
            select(Order, DeliveryStop, Package)
            .outerjoin(DeliveryStop, DeliveryStop.order_id == Order.id)
            .outerjoin(
                Package,
                (Package.delivery_stop_id == DeliveryStop.id) & (Package.order_id == Order.id),
            )
            .options(
                joinedload(Order.created_by),
                joinedload(Order.contact_user),
                joinedload(Order.pickup_address),
            )
            .where(Order.id == order_id)
            .order_by(
                DeliveryStop.created_at.asc(),
                Package.created_at.asc(),
            )
        )
        rows = (await self.session.execute(stmt)).all()
        if not rows:
            return None, [], {}

        order = rows[0][0]
        stops_by_id: dict[str, DeliveryStop] = {}
        packages_by_stop_id: dict[str, list[Package]] = {}
        for _, stop, pkg in rows:
            if stop is not None and stop.id not in stops_by_id:
                stops_by_id[stop.id] = stop
                packages_by_stop_id[stop.id] = []
            if stop is not None and pkg is not None:
                packages_by_stop_id[stop.id].append(pkg)
        return order, list(stops_by_id.values()), packages_by_stop_id

    async def list_for_org(
        self,
        organization_id: str | None,
        *,
        statuses: list[str] | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        pickup_address_expr = func.concat_ws(
            ", ",
            PickupAddress.line_1,
            PickupAddress.line_2,
            PickupAddress.city,
            PickupAddress.postcode,
        )
        contact_name_expr = func.coalesce(PickupAddress.label, "")
        created_from_dt = datetime.combine(date_from, time.min) if date_from else None
        created_to_dt_exclusive = datetime.combine(date_to + timedelta(days=1), time.min) if date_to else None

        creator_user = aliased(User)
        customer_user = aliased(User)

        filters = []
        if organization_id:
            filters.append(Order.organization_id == organization_id)
        if statuses:
            filters.append(Order.status.in_(statuses))
        if created_from_dt:
            filters.append(Order.created_at >= created_from_dt)
        if created_to_dt_exclusive:
            filters.append(Order.created_at < created_to_dt_exclusive)
        if search:
            term = f"%{search.strip()}%"
            filters.append(
                or_(
                    Order.order_id.ilike(term),
                    pickup_address_expr.ilike(term),
                )
            )
        base_query = select(Order.id).outerjoin(
            PickupAddress, PickupAddress.id == Order.pickup_address_id
        )
        if filters:
            base_query = base_query.where(and_(*filters))
        base_from = base_query.subquery()
        count_stmt = select(func.count()).select_from(base_from)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stop_counts_sq = (
            select(
                DeliveryStop.order_id.label("order_id"),
                func.count(DeliveryStop.id).label("delivery_stop_count"),
            )
            .group_by(DeliveryStop.order_id)
            .subquery()
        )
        package_counts_sq = (
            select(
                Package.order_id.label("order_id"),
                func.count(Package.id).label("package_count"),
            )
            .group_by(Package.order_id)
            .subquery()
        )

        client_name_expr = func.nullif(
            func.coalesce(
                func.nullif(func.trim(Organization.trading_name), ""),
                func.nullif(func.trim(Organization.legal_entity_name), ""),
            ),
            "",
        )

        customer_name_expr = func.nullif(
            func.trim(func.concat_ws(" ", customer_user.first_name, customer_user.last_name)),
            "",
        )

        stmt = (
            select(
                Order.id.label("id"),
                Order.order_id.label("order_id"),
                Order.organization_id.label("organization_id"),
                Order.customer_id.label("customer_id"),
                Order.pickup_address_id.label("pickup_address_id"),
                Order.created_by_id.label("created_by_id"),
                Order.status.label("status"),
                Order.created_at.label("created_at"),
                Order.total_amount.label("total_amount"),
                contact_name_expr.label("contact_name"),
                func.coalesce(pickup_address_expr, "").label("pickup_address"),
                PickupAddress.postcode.label("pickup_postcode"),
                func.concat_ws(" ", creator_user.first_name, creator_user.last_name).label("created_by_name"),
                client_name_expr.label("client_name"),
                customer_name_expr.label("customer_name"),
                Organization.reference.label("client_reference"),
                func.coalesce(package_counts_sq.c.package_count, 0).label("package_count"),
                func.coalesce(stop_counts_sq.c.delivery_stop_count, 0).label("delivery_stop_count"),
            )
            .outerjoin(PickupAddress, PickupAddress.id == Order.pickup_address_id)
            .outerjoin(creator_user, creator_user.id == Order.created_by_id)
            .outerjoin(Organization, Organization.id == Order.organization_id)
            .outerjoin(customer_user, customer_user.id == Order.customer_id)
            .outerjoin(stop_counts_sq, stop_counts_sq.c.order_id == Order.id)
            .outerjoin(package_counts_sq, package_counts_sq.c.order_id == Order.id)
        )
        if filters:
            stmt = stmt.where(and_(*filters))
        stmt = stmt.order_by(Order.created_at.desc()).offset(offset).limit(limit)
        rows = (await self.session.execute(stmt)).mappings().all()
        return [dict(row) for row in rows], total

    async def list_stops(self, order_id: str) -> list[DeliveryStop]:
        stmt = select(DeliveryStop).where(DeliveryStop.order_id == order_id).order_by(DeliveryStop.created_at.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_stop(self, order_id: str, stop_id: str) -> DeliveryStop | None:
        stmt = select(DeliveryStop).where(DeliveryStop.id == stop_id, DeliveryStop.order_id == order_id)
        return (await self.session.execute(stmt)).scalars().first()

    async def get_stop_by_tracking(self, tracking_id: str) -> DeliveryStop | None:
        stmt = select(DeliveryStop).where(DeliveryStop.tracking_id == tracking_id)
        return (await self.session.execute(stmt)).scalars().first()

    async def get_stop_by_id(self, stop_id: str) -> DeliveryStop | None:
        stmt = select(DeliveryStop).where(DeliveryStop.id == stop_id)
        return (await self.session.execute(stmt)).scalars().first()

    async def get_stop_with_order(self, stop_id: str) -> tuple[DeliveryStop, Order] | None:
        stmt = (
            select(DeliveryStop, Order)
            .join(Order, Order.id == DeliveryStop.order_id)
            .where(DeliveryStop.id == stop_id)
        )
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return None
        return row[0], row[1]

    async def get_package_with_stop_and_order(
        self,
        package_id: str,
    ) -> tuple[Package, DeliveryStop | None, Order] | None:
        stmt = (
            select(Package, DeliveryStop, Order)
            .join(Order, Order.id == Package.order_id)
            .outerjoin(DeliveryStop, DeliveryStop.id == Package.delivery_stop_id)
            .where(Package.id == package_id)
        )
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return None
        return row[0], row[1], row[2]

    async def list_packages_by_stop_and_statuses(
        self,
        stop_id: str,
        statuses: list[PackageStatus],
    ) -> list[Package]:
        if not statuses:
            return []
        stmt = (
            select(Package)
            .where(Package.delivery_stop_id == stop_id)
            .where(Package.status.in_([s.value for s in statuses]))
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def package_status_breakdown_for_order(self, order_id: str) -> dict[str, int]:
        stmt = (
            select(Package.status, func.count(Package.id))
            .where(Package.order_id == order_id)
            .group_by(Package.status)
        )
        rows = (await self.session.execute(stmt)).all()
        result: dict[str, int] = {}
        for status_value, count in rows:
            key = status_value.value if hasattr(status_value, "value") else str(status_value)
            result[key] = int(count or 0)
        return result

    async def package_status_breakdown_for_stop(self, stop_id: str) -> dict[str, int]:
        stmt = (
            select(Package.status, func.count(Package.id))
            .where(Package.delivery_stop_id == stop_id)
            .group_by(Package.status)
        )
        rows = (await self.session.execute(stmt)).all()
        result: dict[str, int] = {}
        for status_value, count in rows:
            key = status_value.value if hasattr(status_value, "value") else str(status_value)
            result[key] = int(count or 0)
        return result

    async def list_evidence_images_for_stop(self, stop_id: str) -> list[DeliveryStopReturnEvidenceImage]:
        stmt = (
            select(DeliveryStopReturnEvidenceImage)
            .where(DeliveryStopReturnEvidenceImage.delivery_stop_id == stop_id)
            .order_by(DeliveryStopReturnEvidenceImage.sort_order.asc(), DeliveryStopReturnEvidenceImage.created_at.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_failed_attempts_for_stop(
        self, stop_id: str
    ) -> list[DeliveryStopFailedAttempt]:
        stmt = (
            select(DeliveryStopFailedAttempt)
            .where(DeliveryStopFailedAttempt.delivery_stop_id == stop_id)
            .order_by(DeliveryStopFailedAttempt.attempt_number.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_return_attempts_for_stop(
        self, stop_id: str
    ) -> list[DeliveryStopReturnAttempt]:
        stmt = (
            select(DeliveryStopReturnAttempt)
            .where(DeliveryStopReturnAttempt.delivery_stop_id == stop_id)
            .order_by(DeliveryStopReturnAttempt.attempt_number.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_packages_for_stop(self, stop_id: str) -> list[Package]:
        stmt: Select = select(Package).where(Package.delivery_stop_id == stop_id).order_by(Package.created_at.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def count_packages_for_order(self, order_id: str) -> int:
        stmt = select(func.count()).select_from(Package).where(Package.order_id == order_id)
        return (await self.session.execute(stmt)).scalar() or 0

    async def count_stops_for_order(self, order_id: str) -> int:
        stmt = select(func.count()).select_from(DeliveryStop).where(DeliveryStop.order_id == order_id)
        return (await self.session.execute(stmt)).scalar() or 0

    async def order_status_counts(
        self,
        organization_id: str | None,
        *,
        date_from: datetime | None,
        date_to_exclusive: datetime | None,
    ) -> dict[str, int]:
        filters = []
        if organization_id:
            filters.append(Order.organization_id == organization_id)
        if date_from is not None:
            filters.append(Order.created_at >= date_from)
        if date_to_exclusive is not None:
            filters.append(Order.created_at < date_to_exclusive)
        stmt = select(Order.status, func.count(Order.id)).group_by(Order.status)
        if filters:
            stmt = stmt.where(and_(*filters))
        rows = (await self.session.execute(stmt)).all()
        result: dict[str, int] = {}
        for status_value, count in rows:
            key = status_value.value if hasattr(status_value, "value") else str(status_value)
            result[key] = int(count or 0)
        return result

    @staticmethod
    def aggregate_order_card_counts(by_status: dict[str, int]) -> dict[str, int]:
        total = sum(by_status.values())
        pickup_on_route = sum(by_status.get(s.value, 0) for s in PICKUP_ON_ROUTE_ORDER_STATUSES)
        return {
            "total": total,
            "pickups_on_route": pickup_on_route,
            "delivered": by_status.get(OrderStatus.DELIVERED.value, 0),
            "cancelled": by_status.get(OrderStatus.CANCELLED.value, 0),
            "failed": by_status.get(OrderStatus.FAILED.value, 0),
            "returned": by_status.get(OrderStatus.RETURNED.value, 0),
        }

    async def package_status_counts(
        self,
        organization_id: str | None,
        *,
        date_from: datetime | None,
        date_to_exclusive: datetime | None,
        statuses: list[PackageStatus] | None = None,
    ) -> dict[str, int]:
        filters = []
        if organization_id:
            filters.append(Order.organization_id == organization_id)
        if date_from is not None:
            filters.append(Package.created_at >= date_from)
        if date_to_exclusive is not None:
            filters.append(Package.created_at < date_to_exclusive)
        if statuses:
            filters.append(Package.status.in_([s.value for s in statuses]))
        stmt = (
            select(Package.status, func.count(Package.id))
            .join(Order, Order.id == Package.order_id)
            .group_by(Package.status)
        )
        if filters:
            stmt = stmt.where(and_(*filters))
        rows = (await self.session.execute(stmt)).all()
        result: dict[str, int] = {}
        for status_value, count in rows:
            key = status_value.value if hasattr(status_value, "value") else str(status_value)
            result[key] = int(count or 0)
        return result

    async def avg_return_resolution_days(
        self,
        organization_id: str | None,
        *,
        date_from: datetime | None,
        date_to_exclusive: datetime | None,
    ) -> float | None:
        filters: list[Any] = [Package.status.in_([PackageStatus.RETURNED.value, PackageStatus.DISPOSED.value])]
        if organization_id:
            filters.append(Order.organization_id == organization_id)
        if date_from is not None:
            filters.append(Package.created_at >= date_from)
        if date_to_exclusive is not None:
            filters.append(Package.created_at < date_to_exclusive)
        avg_days_expr = func.avg(
            func.extract("epoch", Package.updated_at - Package.created_at) / 86400.0
        )
        stmt = (
            select(avg_days_expr)
            .join(Order, Order.id == Package.order_id)
            .where(and_(*filters))
        )
        value = (await self.session.execute(stmt)).scalar()
        if value is None:
            return None
        return float(value)

    async def list_failed_delivery_stops(
        self,
        organization_id: str | None,
        *,
        package_statuses: list[PackageStatus] | None = None,
        attempt_numbers: list[int] | None = None,
        search: str | None = None,
        date_from: datetime | None = None,
        date_to_exclusive: datetime | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        statuses = list(package_statuses) if package_statuses else list(FAILED_PACKAGE_STATUSES)
        status_values = [s.value for s in statuses]

        attempt_case = case(
            (DeliveryStop.status == DeliveryStopStatus.DELIVERY_ATTEMPT_1_FAILED.value, 1),
            (DeliveryStop.status == DeliveryStopStatus.DELIVERY_ATTEMPT_2_FAILED.value, 2),
            (DeliveryStop.status == DeliveryStopStatus.DELIVERY_ATTEMPT_3_FAILED.value, 3),
            else_=0,
        ).label("attempt_number")

        failed_packages_sq = (
            select(
                Package.delivery_stop_id.label("delivery_stop_id"),
                func.count(Package.id).label("failed_package_count"),
            )
            .where(Package.status.in_(status_values))
            .where(Package.delivery_stop_id.is_not(None))
            .group_by(Package.delivery_stop_id)
            .subquery()
        )

        base_filters = [failed_packages_sq.c.failed_package_count > 0]
        if organization_id:
            base_filters.append(Order.organization_id == organization_id)
        if date_from is not None:
            base_filters.append(DeliveryStop.created_at >= date_from)
        if date_to_exclusive is not None:
            base_filters.append(DeliveryStop.created_at < date_to_exclusive)
        if search:
            term = f"%{search.strip()}%"
            base_filters.append(
                or_(
                    DeliveryStop.tracking_id.ilike(term),
                    DeliveryStop.postcode.ilike(term),
                    Order.order_id.ilike(term),
                )
            )

        if attempt_numbers:
            base_filters.append(attempt_case.in_(attempt_numbers))

        base = (
            select(
                DeliveryStop.id.label("delivery_stop_id"),
                DeliveryStop.tracking_id.label("tracking_id"),
                DeliveryStop.postcode.label("postcode"),
                DeliveryStop.status.label("stop_status"),
                DeliveryStop.updated_at.label("stop_updated_at"),
                DeliveryStop.created_at.label("stop_created_at"),
                Order.id.label("order_id"),
                Order.order_id.label("order_reference"),
                attempt_case,
            )
            .join(Order, Order.id == DeliveryStop.order_id)
            .join(failed_packages_sq, failed_packages_sq.c.delivery_stop_id == DeliveryStop.id)
            .where(and_(*base_filters))
        )

        count_sq = base.with_only_columns(DeliveryStop.id).subquery()
        total = (await self.session.execute(select(func.count()).select_from(count_sq))).scalar() or 0

        stmt = base.order_by(DeliveryStop.updated_at.desc()).offset(offset).limit(limit)
        rows = (await self.session.execute(stmt)).mappings().all()
        return [dict(row) for row in rows], total

    async def packages_for_failed_stops(
        self,
        delivery_stop_ids: list[str],
        *,
        package_statuses: list[PackageStatus] | None = None,
    ) -> list[dict[str, Any]]:
        if not delivery_stop_ids:
            return []
        statuses = list(package_statuses) if package_statuses else list(FAILED_PACKAGE_STATUSES)
        status_values = [s.value for s in statuses]
        reason_sq = (
            select(
                PackageMissingReport.package_id.label("package_id"),
                func.max(PackageMissingReport.created_at).label("latest_at"),
            )
            .group_by(PackageMissingReport.package_id)
            .subquery()
        )
        latest_report = (
            select(
                PackageMissingReport.package_id.label("package_id"),
                PackageMissingReport.reason_code.label("reason_code"),
                PackageMissingReport.details.label("details"),
            )
            .join(
                reason_sq,
                and_(
                    PackageMissingReport.package_id == reason_sq.c.package_id,
                    PackageMissingReport.created_at == reason_sq.c.latest_at,
                ),
            )
            .subquery()
        )
        stmt = (
            select(
                Package.id.label("package_pk"),
                Package.package_id.label("package_id"),
                Package.status.label("status"),
                Package.delivery_stop_id.label("delivery_stop_id"),
                latest_report.c.reason_code,
                latest_report.c.details,
            )
            .outerjoin(latest_report, latest_report.c.package_id == Package.id)
            .where(Package.delivery_stop_id.in_(delivery_stop_ids))
            .where(Package.status.in_(status_values))
            .order_by(Package.created_at.asc())
        )
        rows = (await self.session.execute(stmt)).mappings().all()
        return [dict(row) for row in rows]

    async def list_return_stops(
        self,
        organization_id: str | None,
        *,
        package_statuses: list[PackageStatus] | None = None,
        attempt_numbers: list[int] | None = None,
        search: str | None = None,
        date_from: datetime | None = None,
        date_to_exclusive: datetime | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        statuses = list(package_statuses) if package_statuses else list(RETURN_PACKAGE_STATUSES)
        status_values = [s.value for s in statuses]

        attempt_case = case(
            (DeliveryStop.status == DeliveryStopStatus.DELIVERY_ATTEMPT_1_FAILED.value, 1),
            (DeliveryStop.status == DeliveryStopStatus.DELIVERY_ATTEMPT_2_FAILED.value, 2),
            (DeliveryStop.status == DeliveryStopStatus.DELIVERY_ATTEMPT_3_FAILED.value, 3),
            else_=0,
        ).label("attempt_number")

        return_packages_sq = (
            select(
                Package.delivery_stop_id.label("delivery_stop_id"),
                func.count(Package.id).label("return_package_count"),
                func.min(Package.updated_at).label("initiated_at"),
            )
            .where(Package.status.in_(status_values))
            .where(Package.delivery_stop_id.is_not(None))
            .group_by(Package.delivery_stop_id)
            .subquery()
        )

        base_filters = [return_packages_sq.c.return_package_count > 0]
        if organization_id:
            base_filters.append(Order.organization_id == organization_id)
        if date_from is not None:
            base_filters.append(return_packages_sq.c.initiated_at >= date_from)
        if date_to_exclusive is not None:
            base_filters.append(return_packages_sq.c.initiated_at < date_to_exclusive)
        if search:
            term = f"%{search.strip()}%"
            base_filters.append(
                or_(
                    DeliveryStop.tracking_id.ilike(term),
                    DeliveryStop.postcode.ilike(term),
                    Order.order_id.ilike(term),
                )
            )

        if attempt_numbers:
            base_filters.append(attempt_case.in_(attempt_numbers))

        base = (
            select(
                DeliveryStop.id.label("delivery_stop_id"),
                DeliveryStop.tracking_id.label("tracking_id"),
                DeliveryStop.postcode.label("postcode"),
                DeliveryStop.status.label("stop_status"),
                Order.id.label("order_id"),
                Order.order_id.label("order_reference"),
                return_packages_sq.c.initiated_at.label("initiated_at"),
                attempt_case,
            )
            .join(Order, Order.id == DeliveryStop.order_id)
            .join(return_packages_sq, return_packages_sq.c.delivery_stop_id == DeliveryStop.id)
            .where(and_(*base_filters))
        )

        count_sq = base.with_only_columns(DeliveryStop.id).subquery()
        total = (await self.session.execute(select(func.count()).select_from(count_sq))).scalar() or 0

        stmt = base.order_by(return_packages_sq.c.initiated_at.desc()).offset(offset).limit(limit)
        rows = (await self.session.execute(stmt)).mappings().all()
        return [dict(row) for row in rows], total

    async def packages_for_return_stops(
        self,
        delivery_stop_ids: list[str],
        *,
        package_statuses: list[PackageStatus] | None = None,
    ) -> list[dict[str, Any]]:
        if not delivery_stop_ids:
            return []
        statuses = list(package_statuses) if package_statuses else list(RETURN_PACKAGE_STATUSES)
        status_values = [s.value for s in statuses]
        reason_sq = (
            select(
                PackageMissingReport.package_id.label("package_id"),
                func.max(PackageMissingReport.created_at).label("latest_at"),
            )
            .group_by(PackageMissingReport.package_id)
            .subquery()
        )
        latest_report = (
            select(
                PackageMissingReport.package_id.label("package_id"),
                PackageMissingReport.reason_code.label("reason_code"),
                PackageMissingReport.details.label("details"),
            )
            .join(
                reason_sq,
                and_(
                    PackageMissingReport.package_id == reason_sq.c.package_id,
                    PackageMissingReport.created_at == reason_sq.c.latest_at,
                ),
            )
            .subquery()
        )
        stmt = (
            select(
                Package.id.label("package_pk"),
                Package.package_id.label("package_id"),
                Package.status.label("status"),
                Package.delivery_stop_id.label("delivery_stop_id"),
                Package.updated_at.label("initiated_at"),
                latest_report.c.reason_code,
                latest_report.c.details,
            )
            .outerjoin(latest_report, latest_report.c.package_id == Package.id)
            .where(Package.delivery_stop_id.in_(delivery_stop_ids))
            .where(Package.status.in_(status_values))
            .order_by(Package.created_at.asc())
        )
        rows = (await self.session.execute(stmt)).mappings().all()
        return [dict(row) for row in rows]


class StopNoteRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__(session, StopNote)

    async def list_for_delivery_stop(self, delivery_stop_id: str) -> list[StopNote]:
        stmt = select(StopNote).where(StopNote.delivery_stop_id == delivery_stop_id).order_by(StopNote.sort_order.asc(), StopNote.created_at.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_images_for_note_ids(self, note_ids: list[str]) -> list[StopNoteImage]:
        if not note_ids:
            return []
        stmt = select(StopNoteImage).where(StopNoteImage.stop_note_id.in_(note_ids)).order_by(StopNoteImage.sort_order.asc(), StopNoteImage.created_at.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_for_stop(self, *, note_id: str, delivery_stop_id: str) -> StopNote | None:
        stmt = select(StopNote).where(
            StopNote.id == note_id,
            StopNote.delivery_stop_id == delivery_stop_id,
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def list_images_for_note(self, note_id: str) -> list[StopNoteImage]:
        stmt = (
            select(StopNoteImage)
            .where(StopNoteImage.stop_note_id == note_id)
            .order_by(StopNoteImage.sort_order.asc(), StopNoteImage.created_at.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_image_for_note(self, *, note_id: str, image_id: str) -> StopNoteImage | None:
        stmt = select(StopNoteImage).where(
            StopNoteImage.id == image_id,
            StopNoteImage.stop_note_id == note_id,
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def get_ack(
        self,
        *,
        delivery_stop_id: str,
        driver_id: str,
        notes_hash: str,
    ) -> StopNoteAcknowledgement | None:
        stmt = select(StopNoteAcknowledgement).where(
            StopNoteAcknowledgement.delivery_stop_id == delivery_stop_id,
            StopNoteAcknowledgement.driver_id == driver_id,
            StopNoteAcknowledgement.notes_hash == notes_hash,
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def upsert_ack(
        self,
        *,
        delivery_stop_id: str,
        driver_id: str,
        notes_hash: str,
        acknowledged_at,
    ) -> StopNoteAcknowledgement:
        existing = await self.get_ack(
            delivery_stop_id=delivery_stop_id,
            driver_id=driver_id,
            notes_hash=notes_hash,
        )
        if existing is not None:
            return existing
        row = StopNoteAcknowledgement(
            delivery_stop_id=delivery_stop_id,
            driver_id=driver_id,
            notes_hash=notes_hash,
            acknowledged_at=acknowledged_at,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row


class OrderEventRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__(session, OrderEvent)

    async def list_by_order(self, order_id: str) -> list[OrderEvent]:
        stmt = (
            select(OrderEvent)
            .where(OrderEvent.order_id == order_id)
            .order_by(OrderEvent.created_at.asc(), OrderEvent.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())


class DeliveryStopEventRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__(session, DeliveryStopEvent)

    async def list_by_delivery_stop(self, delivery_stop_id: str) -> list[DeliveryStopEvent]:
        stmt = (
            select(DeliveryStopEvent)
            .where(DeliveryStopEvent.delivery_stop_id == delivery_stop_id)
            .order_by(DeliveryStopEvent.created_at.asc(), DeliveryStopEvent.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_by_delivery_stop_ids(self, delivery_stop_ids: list[str]) -> list[DeliveryStopEvent]:
        if not delivery_stop_ids:
            return []
        stmt = (
            select(DeliveryStopEvent)
            .where(DeliveryStopEvent.delivery_stop_id.in_(delivery_stop_ids))
            .order_by(
                DeliveryStopEvent.delivery_stop_id.asc(),
                DeliveryStopEvent.created_at.asc(),
                DeliveryStopEvent.id.asc(),
            )
        )
        return list((await self.session.execute(stmt)).scalars().all())


class PackageEventRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__(session, PackageEvent)

    async def list_by_package(self, package_id: str) -> list[PackageEvent]:
        stmt = (
            select(PackageEvent)
            .where(PackageEvent.package_id == package_id)
            .order_by(PackageEvent.created_at.asc(), PackageEvent.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_by_package_ids(self, package_ids: list[str]) -> list[PackageEvent]:
        if not package_ids:
            return []
        stmt = (
            select(PackageEvent)
            .where(PackageEvent.package_id.in_(package_ids))
            .order_by(PackageEvent.package_id.asc(), PackageEvent.created_at.asc(), PackageEvent.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())


class PackageExecutionRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Package)

    async def resolve_by_scan_value(self, scan_value: str) -> Package | None:
        filters = [Package.package_id == scan_value]
        try:
            UUID(scan_value)
            filters.append(Package.id == scan_value)
        except ValueError:
            pass
        stmt = select(Package).where(or_(*filters))
        return (await self.session.execute(stmt)).scalars().first()

    async def list_for_delivery_stop(self, delivery_stop_id: str) -> list[Package]:
        stmt = select(Package).where(Package.delivery_stop_id == delivery_stop_id).order_by(Package.created_at.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_for_order(self, order_id: str) -> list[Package]:
        """Return every package belonging to ``order_id`` in creation order.

        Used by pickup-flow stops where the route stop references an Order (via ``order_id``)
        rather than an individual delivery stop.
        """
        stmt = select(Package).where(Package.order_id == order_id).order_by(Package.created_at.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_for_delivery_stop_by_package_ids(
        self,
        delivery_stop_id: str,
        package_ids: list[str],
    ) -> list[Package]:
        """Return packages on this delivery stop whose ids are in ``package_ids`` (no strict ordering)."""
        if not package_ids:
            return []
        stmt = select(Package).where(
            Package.delivery_stop_id == delivery_stop_id,
            Package.id.in_(package_ids),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create_scan_log(
        self,
        *,
        route_id: str,
        route_stop_id: str,
        delivery_stop_id: str,
        package_id: str | None,
        driver_id: str,
        scan_value: str,
        result: str,
    ) -> PackageScanLog:
        row = PackageScanLog(
            route_id=route_id,
            route_stop_id=route_stop_id,
            delivery_stop_id=delivery_stop_id,
            package_id=package_id,
            driver_id=driver_id,
            scan_value=scan_value,
            result=result,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def update_package_status(
        self,
        *,
        package: Package,
        status: PackageStatus,
        actor_user_id: str | None = None,
        suppress_automation: bool = False,
    ) -> Package:
        previous = package.status
        if previous == status:
            await self.session.flush()
            await self.session.refresh(package)
            return package
        from app.modules.orders.service import OrderStatusEventService

        package.status = status
        event_row = OrderStatusEventService(self.session).record_package_transition(
            package_id=package.id,
            from_status=previous,
            to_status=status,
            actor_user_id=actor_user_id,
        )
        await self.session.flush()
        await self.session.refresh(package)
        if not suppress_automation:
            org_id = (
                await self.session.execute(
                    select(Order.organization_id).where(Order.id == package.order_id)
                )
            ).scalar_one_or_none()
            if org_id:
                await enqueue(
                    Job.EVALUATE_STATUS_AUTOMATION_RULES,
                    {
                        "event_id": str(event_row.id),
                        "occurred_at": event_row.created_at.isoformat() if event_row.created_at else None,
                        "organization_id": str(org_id),
                        "entity_type": "PACKAGE",
                        "entity_id": str(package.id),
                        "order_id": str(package.order_id),
                        "delivery_stop_id": str(package.delivery_stop_id) if package.delivery_stop_id else None,
                        "from_status": previous.value if hasattr(previous, "value") else str(previous),
                        "to_status": status.value if hasattr(status, "value") else str(status),
                        "actor_user_id": actor_user_id,
                    },
                    priority=QueuePriority.DEFAULT,
                    _job_id=f"status-auto:{event_row.id}",
                )
        return package

    async def create_missing_report(
        self,
        *,
        package_id: str,
        route_id: str,
        route_stop_id: str,
        delivery_stop_id: str,
        driver_id: str,
        reason_code: str,
        details: str | None,
    ) -> PackageMissingReport:
        row = PackageMissingReport(
            package_id=package_id,
            route_id=route_id,
            route_stop_id=route_stop_id,
            delivery_stop_id=delivery_stop_id,
            driver_id=driver_id,
            reason_code=reason_code,
            details=details,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row
