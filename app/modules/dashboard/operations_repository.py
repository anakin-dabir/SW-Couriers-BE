"""Data access for operations dashboard KPIs."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dashboard.constants import (
    DELIVERED_STOP_EVENT_STATUSES,
    FAILED_STOP_EVENT_STATUSES,
    TERMINAL_ORDER_STATUSES,
)
from app.modules.dashboard.utils import DayWindow
from app.modules.drivers.enums import DriverAccountStatus
from app.modules.drivers.models import Driver
from app.modules.orders.models import DeliveryStop, DeliveryStopEvent, Order
from app.modules.planning.models import Route, RoutePlan, RouteStop


class OperationsDashboardRepository:
    """Aggregates for the admin operations home dashboard KPI cards."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _order_org_filter(organization_id: str | None):
        if not organization_id:
            return None
        return Order.organization_id == organization_id

    async def count_route_stops_for_service_dates(
        self,
        organization_id: str | None,
        *,
        start: date,
        end: date,
    ) -> int:
        stmt = (
            select(func.count(RouteStop.id))
            .select_from(RouteStop)
            .join(Route, Route.id == RouteStop.route_id)
            .join(RoutePlan, RoutePlan.id == Route.plan_id)
            .where(
                RoutePlan.service_date >= start,
                RoutePlan.service_date <= end,
            )
        )
        if organization_id:
            stmt = (
                stmt.outerjoin(DeliveryStop, DeliveryStop.id == RouteStop.delivery_stop_id)
                .outerjoin(
                    Order,
                    or_(
                        Order.id == RouteStop.order_id,
                        Order.id == DeliveryStop.order_id,
                    ),
                )
                .where(Order.organization_id == organization_id)
            )
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    async def count_distinct_delivery_stop_events(
        self,
        organization_id: str | None,
        *,
        window: DayWindow,
        to_statuses: frozenset[str],
    ) -> int:
        stmt = (
            select(func.count(func.distinct(DeliveryStopEvent.delivery_stop_id)))
            .select_from(DeliveryStopEvent)
            .join(DeliveryStop, DeliveryStop.id == DeliveryStopEvent.delivery_stop_id)
            .join(Order, Order.id == DeliveryStop.order_id)
            .where(
                DeliveryStopEvent.to_status.in_(tuple(to_statuses)),
                DeliveryStopEvent.created_at >= window.start,
                DeliveryStopEvent.created_at < window.end_exclusive,
            )
        )
        org_filter = self._order_org_filter(organization_id)
        if org_filter is not None:
            stmt = stmt.where(org_filter)
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    async def count_orders_created_in_window(
        self,
        organization_id: str | None,
        *,
        window: DayWindow,
    ) -> int:
        filters = [
            Order.created_at >= window.start,
            Order.created_at < window.end_exclusive,
        ]
        if organization_id:
            filters.append(Order.organization_id == organization_id)
        stmt = select(func.count(Order.id)).where(and_(*filters))
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    async def count_pending_orders(
        self,
        organization_id: str | None,
        *,
        created_before: datetime | None = None,
    ) -> int:
        terminal = tuple(s.value for s in TERMINAL_ORDER_STATUSES)
        filters = [Order.status.not_in(terminal)]
        if organization_id:
            filters.append(Order.organization_id == organization_id)
        if created_before is not None:
            filters.append(Order.created_at < created_before)
        stmt = select(func.count(Order.id)).where(and_(*filters))
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)

    async def count_active_drivers(
        self,
        *,
        created_before: datetime | None = None,
    ) -> int:
        filters = [
            Driver.account_status == DriverAccountStatus.ACTIVE.value,
            Driver.user_id.isnot(None),
        ]
        if created_before is not None:
            filters.append(Driver.created_at < created_before)
        stmt = select(func.count(Driver.id)).where(and_(*filters))
        result = await self._session.execute(stmt)
        return int(result.scalar() or 0)
