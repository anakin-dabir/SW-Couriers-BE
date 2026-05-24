"""Operations dashboard KPI service."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.service import BaseService
from app.modules.dashboard.constants import DELIVERED_STOP_EVENT_STATUSES, FAILED_STOP_EVENT_STATUSES
from app.modules.dashboard.operations_repository import OperationsDashboardRepository
from app.modules.dashboard.types import (
    CountKpiResult,
    DeliveredTodayKpiResult,
    OperationsDashboardResult,
)
from app.modules.dashboard.utils import pct_change, success_rate_pct, utc_day_window
from app.modules.dashboard.validation import resolve_as_of_date, validate_as_of_date


class OperationsDashboardService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._repo = OperationsDashboardRepository(session)

    async def get_operations_kpis(
        self,
        *,
        organization_id: str | None,
        as_of_date: date | None = None,
    ) -> OperationsDashboardResult:
        today = validate_as_of_date(resolve_as_of_date(as_of_date))
        today_window = utc_day_window(today)
        yesterday = today - timedelta(days=1)
        yesterday_window = utc_day_window(yesterday)

        next_start = today
        next_end = today + timedelta(days=6)
        prev_next_start = today - timedelta(days=7)
        prev_next_end = today - timedelta(days=1)

        next_current = await self._repo.count_route_stops_for_service_dates(
            organization_id,
            start=next_start,
            end=next_end,
        )
        next_previous = await self._repo.count_route_stops_for_service_dates(
            organization_id,
            start=prev_next_start,
            end=prev_next_end,
        )

        delivered_current = await self._repo.count_distinct_delivery_stop_events(
            organization_id,
            window=today_window,
            to_statuses=DELIVERED_STOP_EVENT_STATUSES,
        )
        delivered_previous = await self._repo.count_distinct_delivery_stop_events(
            organization_id,
            window=yesterday_window,
            to_statuses=DELIVERED_STOP_EVENT_STATUSES,
        )
        failed_current = await self._repo.count_distinct_delivery_stop_events(
            organization_id,
            window=today_window,
            to_statuses=FAILED_STOP_EVENT_STATUSES,
        )
        failed_previous = await self._repo.count_distinct_delivery_stop_events(
            organization_id,
            window=yesterday_window,
            to_statuses=FAILED_STOP_EVENT_STATUSES,
        )

        orders_current = await self._repo.count_orders_created_in_window(
            organization_id,
            window=today_window,
        )
        orders_previous = await self._repo.count_orders_created_in_window(
            organization_id,
            window=yesterday_window,
        )

        pending_current = await self._repo.count_pending_orders(organization_id)
        pending_previous = await self._repo.count_pending_orders(
            organization_id,
            created_before=today_window.start,
        )

        drivers_current = await self._repo.count_active_drivers()
        drivers_previous = await self._repo.count_active_drivers(created_before=today_window.start)

        return OperationsDashboardResult(
            as_of_date=today,
            organization_id=organization_id,
            next_7_day_stops=_count_kpi(next_current, next_previous, comparison_label="last 7 days"),
            delivered_today=_delivered_kpi(
                delivered_current,
                delivered_previous,
                failed_current,
                failed_previous,
            ),
            today_orders=_count_kpi(orders_current, orders_previous, comparison_label="yesterday"),
            pending_orders=_count_kpi(pending_current, pending_previous, comparison_label="yesterday"),
            active_drivers=_count_kpi(drivers_current, drivers_previous, comparison_label="yesterday"),
        )


def _count_kpi(current: int, previous: int, *, comparison_label: str) -> CountKpiResult:
    return CountKpiResult(
        current=current,
        previous=previous,
        change_abs=current - previous,
        change_pct=pct_change(float(current), float(previous)),
        comparison_label=comparison_label,
    )


def _delivered_kpi(
    delivered_current: int,
    delivered_previous: int,
    failed_current: int,
    failed_previous: int,
) -> DeliveredTodayKpiResult:
    return DeliveredTodayKpiResult(
        current=delivered_current,
        previous=delivered_previous,
        change_abs=delivered_current - delivered_previous,
        change_pct=pct_change(float(delivered_current), float(delivered_previous)),
        success_rate_pct=success_rate_pct(delivered_current, failed_current),
        previous_success_rate_pct=success_rate_pct(delivered_previous, failed_previous),
        comparison_label="yesterday",
    )
