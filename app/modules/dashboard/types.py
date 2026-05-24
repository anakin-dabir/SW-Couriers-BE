"""Shared result types for dashboard services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CountKpiResult:
    current: int
    previous: int
    change_abs: int
    change_pct: float | None
    comparison_label: str


@dataclass(frozen=True, slots=True)
class DeliveredTodayKpiResult:
    current: int
    previous: int
    change_abs: int
    change_pct: float | None
    success_rate_pct: float | None
    previous_success_rate_pct: float | None
    comparison_label: str


@dataclass(frozen=True, slots=True)
class OperationsDashboardResult:
    as_of_date: date
    organization_id: str | None
    next_7_day_stops: CountKpiResult
    delivered_today: DeliveredTodayKpiResult
    today_orders: CountKpiResult
    pending_orders: CountKpiResult
    active_drivers: CountKpiResult


@dataclass(frozen=True, slots=True)
class RevenueTrendDayResult:
    date: date
    weekday: str
    revenue: Decimal


@dataclass(frozen=True, slots=True)
class TodaysFinancialsResult:
    as_of_date: date
    organization_id: str | None
    revenue_today: Decimal
    unpaid_invoices_count: int
    overdue_invoices_count: int
    revenue_trend: tuple[RevenueTrendDayResult, ...]
