"""Repository for highlighted operational issues on the admin dashboard."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.dashboard.constants import (
    FAILED_ATTEMPT_STOP_STATUSES,
    HIGHLIGHTED_ORDER_STATUSES,
    HIGHLIGHTED_STOP_STATUSES,
)
from app.modules.drivers.models import Driver
from app.modules.orders.enums import DeliveryStopStatus, PackageStatus
from app.modules.orders.models import DeliveryStop, DeliveryStopEvent, Order, Package
from app.modules.organizations.models import Organization
from app.modules.planning.models import Route, RouteStop
from app.modules.status_automation_rules.enums import EntityType
from app.modules.status_automation_rules.models import StatusAutomationExecutionLog
from app.modules.user.models import User


class HighlightedIssuesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_highlighted_issues(
        self,
        organization_id: str | None,
        *,
        search: str | None,
        stop_statuses: list[str] | None,
        today: date,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        driver_user = aliased(User)
        route = aliased(Route)

        route_stop_subq = (
            select(
                RouteStop.delivery_stop_id.label("delivery_stop_id"),
                RouteStop.route_id.label("route_id"),
                route.driver_id.label("route_driver_id"),
            )
            .select_from(RouteStop)
            .join(route, route.id == RouteStop.route_id)
            .where(RouteStop.delivery_stop_id.isnot(None))
            .subquery()
        )

        customer_not_home = exists(
            select(Package.id).where(
                Package.delivery_stop_id == DeliveryStop.id,
                Package.status == PackageStatus.CUSTOMER_NOT_HOME.value,
            )
        )

        issue_predicate = or_(
            and_(route_stop_subq.c.route_id.isnot(None), route_stop_subq.c.route_driver_id.is_(None)),
            DeliveryStop.status.in_(tuple(FAILED_ATTEMPT_STOP_STATUSES)),
            Order.status.in_(tuple(HIGHLIGHTED_ORDER_STATUSES)),
            customer_not_home,
            and_(
                DeliveryStop.scheduled_for.isnot(None),
                DeliveryStop.scheduled_for < today,
                DeliveryStop.status == DeliveryStopStatus.OUT_FOR_DELIVERY.value,
            ),
        )

        filters = [
            or_(
                DeliveryStop.status.in_(tuple(HIGHLIGHTED_STOP_STATUSES)),
                Order.status.in_(tuple(HIGHLIGHTED_ORDER_STATUSES)),
            ),
            issue_predicate,
        ]
        if organization_id:
            filters.append(Order.organization_id == organization_id)
        if stop_statuses:
            filters.append(DeliveryStop.status.in_(stop_statuses))
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    DeliveryStop.tracking_id.ilike(pattern),
                    Organization.trading_name.ilike(pattern),
                    func.concat(DeliveryStop.recipient_first_name, " ", DeliveryStop.recipient_last_name).ilike(pattern),
                    Order.order_id.ilike(pattern),
                )
            )

        base = (
            select(
                DeliveryStop.id.label("delivery_stop_id"),
                DeliveryStop.tracking_id,
                DeliveryStop.status.label("stop_status"),
                DeliveryStop.scheduled_for.label("delivery_deadline"),
                Order.id.label("order_id"),
                Order.order_id.label("order_reference"),
                Order.status.label("order_status"),
                Organization.trading_name.label("organization_name"),
                DeliveryStop.recipient_first_name,
                DeliveryStop.recipient_last_name,
                route_stop_subq.c.route_driver_id,
                Driver.driver_code,
                driver_user.first_name.label("driver_first_name"),
                driver_user.last_name.label("driver_last_name"),
                customer_not_home.label("has_customer_not_home"),
            )
            .join(Order, Order.id == DeliveryStop.order_id)
            .join(Organization, Organization.id == Order.organization_id)
            .outerjoin(route_stop_subq, route_stop_subq.c.delivery_stop_id == DeliveryStop.id)
            .outerjoin(Driver, Driver.id == route_stop_subq.c.route_driver_id)
            .outerjoin(driver_user, driver_user.id == Driver.user_id)
            .where(and_(*filters))
        )

        count_stmt = select(func.count()).select_from(base.subquery())
        total = int((await self._session.execute(count_stmt)).scalar_one() or 0)

        rows_stmt = (
            base.order_by(DeliveryStop.scheduled_for.asc().nulls_last(), DeliveryStop.tracking_id.asc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self._session.execute(rows_stmt)).mappings().all()
        stop_ids = [str(r["delivery_stop_id"]) for r in rows]
        remediation_by_stop = await self._latest_remediation_by_stop(stop_ids)
        event_by_stop = await self._latest_stop_event_by_stop(stop_ids)

        items: list[dict[str, Any]] = []
        for row in rows:
            sid = str(row["delivery_stop_id"])
            items.append(
                {
                    **dict(row),
                    "automation_remediation": remediation_by_stop.get(sid),
                    "latest_event_to_status": event_by_stop.get(sid),
                }
            )
        return items, total

    async def _latest_remediation_by_stop(self, stop_ids: list[str]) -> dict[str, str | None]:
        if not stop_ids:
            return {}
        ranked = (
            select(
                StatusAutomationExecutionLog.entity_id,
                StatusAutomationExecutionLog.message,
                func.row_number()
                .over(
                    partition_by=StatusAutomationExecutionLog.entity_id,
                    order_by=StatusAutomationExecutionLog.created_at.desc(),
                )
                .label("rn"),
            )
            .where(
                StatusAutomationExecutionLog.entity_type == EntityType.DELIVERY_STOP.value,
                StatusAutomationExecutionLog.entity_id.in_(stop_ids),
                StatusAutomationExecutionLog.status == "SUCCESS",
            )
            .subquery()
        )
        stmt = select(ranked.c.entity_id, ranked.c.message).where(ranked.c.rn == 1)
        rows = (await self._session.execute(stmt)).all()
        return {str(entity_id): message for entity_id, message in rows}

    async def _latest_stop_event_by_stop(self, stop_ids: list[str]) -> dict[str, str | None]:
        if not stop_ids:
            return {}
        ranked = (
            select(
                DeliveryStopEvent.delivery_stop_id,
                DeliveryStopEvent.to_status,
                func.row_number()
                .over(
                    partition_by=DeliveryStopEvent.delivery_stop_id,
                    order_by=DeliveryStopEvent.created_at.desc(),
                )
                .label("rn"),
            )
            .where(DeliveryStopEvent.delivery_stop_id.in_(stop_ids))
            .subquery()
        )
        stmt = select(ranked.c.delivery_stop_id, ranked.c.to_status).where(ranked.c.rn == 1)
        rows = (await self._session.execute(stmt)).all()
        return {str(stop_id): to_status for stop_id, to_status in rows}
