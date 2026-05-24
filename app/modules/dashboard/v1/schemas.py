"""Dashboard API schemas."""

from __future__ import annotations

from datetime import date

from pydantic import Field, field_validator

from app.common.schemas import BaseSchema


class DashboardCountKpi(BaseSchema):
    current: int = Field(default=0, ge=0)
    previous: int = Field(default=0, ge=0)
    change_abs: int = Field(default=0, description="Absolute delta (current minus previous) for UI copy like +5")
    change_pct: float | None = Field(
        default=None,
        description="Percent change vs comparison period; null when previous is zero",
    )
    comparison_label: str = Field(
        ...,
        description="Human label for the comparison window (e.g. yesterday, last 7 days)",
    )

    @field_validator("change_abs")
    @classmethod
    def change_abs_matches_delta(cls, value: int, info) -> int:
        current = info.data.get("current", 0)
        previous = info.data.get("previous", 0)
        if value != current - previous:
            raise ValueError("change_abs must equal current minus previous")
        return value


class DeliveredTodayKpi(BaseSchema):
    current: int = Field(default=0, ge=0)
    previous: int = Field(default=0, ge=0)
    change_abs: int = 0
    change_pct: float | None = None
    success_rate_pct: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Delivered / (delivered + failed delivery outcomes) today, as a percentage",
    )
    previous_success_rate_pct: float | None = Field(default=None, ge=0, le=100)
    comparison_label: str = "yesterday"

    @field_validator("change_abs")
    @classmethod
    def change_abs_matches_delta(cls, value: int, info) -> int:
        current = info.data.get("current", 0)
        previous = info.data.get("previous", 0)
        if value != current - previous:
            raise ValueError("change_abs must equal current minus previous")
        return value


class OperationsDashboardKpisResponse(BaseSchema):
    as_of_date: date
    organization_id: str | None = Field(
        default=None,
        description="When set, all order/stop metrics are scoped to this organisation; omitted for global admin view",
    )
    next_7_day_stops: DashboardCountKpi
    delivered_today: DeliveredTodayKpi
    today_orders: DashboardCountKpi
    pending_orders: DashboardCountKpi
    active_drivers: DashboardCountKpi


class RevenueTrendDay(BaseSchema):
    date: date
    weekday: str = Field(description="English weekday label, e.g. Monday")
    revenue: str = Field(description="Payments received on this day (GBP, 2dp)")


class TodaysFinancialsResponse(BaseSchema):
    as_of_date: date
    organization_id: str | None = None
    revenue_today: str = Field(description="Payments received on as_of_date")
    unpaid_invoices_count: int = Field(ge=0, description="Sent invoices that are UNPAID or PARTIALLY_PAID")
    overdue_invoices_count: int = Field(ge=0, description="Sent invoices past due or marked OVERDUE")
    revenue_trend: list[RevenueTrendDay] = Field(
        description="Daily payments received for the 7-day window ending on as_of_date",
    )


class HighlightedIssueItem(BaseSchema):
    delivery_stop_id: str
    tracking_number: str
    order_id: str
    order_reference: str
    client_name: str
    status: str = Field(description="Current delivery stop status code")
    driver_name: str | None = None
    delivery_deadline: date | None = None
    deadline_urgency: str = Field(description="critical | warning | normal | none")
    issue: str = Field(description="Human-readable issue label for the table")
    issue_code: str = Field(description="Stable machine code for the issue type")
    auto_remediation: str
    remediation_applied: bool = Field(
        description="True when an automation rule or reschedule action has been recorded",
    )


class HighlightedIssuesListResponse(BaseSchema):
    items: list[HighlightedIssueItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    size: int = Field(ge=1, le=100)
    pages: int = Field(ge=0)
