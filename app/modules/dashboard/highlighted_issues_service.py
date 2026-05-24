"""Business rules for highlighted operational issues."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.service import BaseService
from app.modules.dashboard.constants import FAILED_ATTEMPT_STOP_STATUSES
from app.modules.dashboard.highlighted_issues_repository import HighlightedIssuesRepository
from app.modules.dashboard.validation import (
    normalize_search,
    resolve_as_of_date,
    validate_as_of_date,
    validate_delivery_stop_status_filters,
    validate_pagination,
)
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus, attempt_number_from_stop_status


@dataclass(frozen=True, slots=True)
class HighlightedIssueRow:
    delivery_stop_id: str
    tracking_number: str
    order_id: str
    order_reference: str
    client_name: str
    status: str
    driver_name: str | None
    delivery_deadline: date | None
    deadline_urgency: str
    issue: str
    issue_code: str
    auto_remediation: str
    remediation_applied: bool


class HighlightedIssuesService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._repo = HighlightedIssuesRepository(session)

    async def list_highlighted_issues(
        self,
        organization_id: str | None,
        *,
        search: str | None,
        status: list[str] | None,
        page: int,
        size: int,
        as_of_date: date | None = None,
    ) -> tuple[list[HighlightedIssueRow], int]:
        page, size = validate_pagination(page, size)
        today = validate_as_of_date(resolve_as_of_date(as_of_date))
        offset = (page - 1) * size
        raw_rows, total = await self._repo.list_highlighted_issues(
            organization_id,
            search=normalize_search(search),
            stop_statuses=validate_delivery_stop_status_filters(status),
            today=today,
            offset=offset,
            limit=size,
        )
        return [_to_row(row, today=today) for row in raw_rows], total


def _to_row(raw: dict[str, Any], *, today: date) -> HighlightedIssueRow:
    stop_status = str(raw["stop_status"])
    order_status = str(raw["order_status"])
    route_driver_id = raw.get("route_driver_id")
    scheduled_for = raw.get("delivery_deadline")
    issue_code, issue_label = _detect_issue(
        stop_status=stop_status,
        order_status=order_status,
        route_driver_id=route_driver_id,
        scheduled_for=scheduled_for,
        today=today,
        has_customer_not_home=bool(raw.get("has_customer_not_home")),
    )
    remediation, applied = _format_remediation(
        automation_message=raw.get("automation_remediation"),
        latest_event_to=raw.get("latest_event_to_status"),
        scheduled_for=scheduled_for,
        today=today,
    )
    client = " ".join(p for p in (raw.get("recipient_first_name"), raw.get("recipient_last_name")) if p).strip()
    if not client:
        client = str(raw.get("organization_name") or "—")
    driver_name = _format_driver_name(raw)
    deadline_urgency = _deadline_urgency(scheduled_for, today=today)
    return HighlightedIssueRow(
        delivery_stop_id=str(raw["delivery_stop_id"]),
        tracking_number=str(raw["tracking_id"]),
        order_id=str(raw["order_id"]),
        order_reference=str(raw["order_reference"]),
        client_name=client,
        status=stop_status,
        driver_name=driver_name,
        delivery_deadline=scheduled_for,
        deadline_urgency=deadline_urgency,
        issue=issue_label,
        issue_code=issue_code,
        auto_remediation=remediation,
        remediation_applied=applied,
    )


def _detect_issue(
    *,
    stop_status: str,
    order_status: str,
    route_driver_id: str | None,
    scheduled_for: date | None,
    today: date,
    has_customer_not_home: bool,
) -> tuple[str, str]:
    if has_customer_not_home:
        return "CUSTOMER_NOT_AVAILABLE", "Customer not available"
    if route_driver_id is None and stop_status in {
        DeliveryStopStatus.OUT_FOR_DELIVERY.value,
        DeliveryStopStatus.LOADED_FOR_DELIVERY.value,
        DeliveryStopStatus.DELIVERY_SCHEDULED.value,
        DeliveryStopStatus.PICKUP_SCHEDULED.value,
        DeliveryStopStatus.ENROUTE_PICKUP.value,
    }:
        return "NO_DRIVER_ASSIGNED", "No driver assigned"
    if stop_status in FAILED_ATTEMPT_STOP_STATUSES:
        attempt = attempt_number_from_stop_status(DeliveryStopStatus(stop_status)) or 1
        return "DELIVERY_ATTEMPT_FAILED", f"Day {attempt}/3 completed"
    if stop_status in {
        DeliveryStopStatus.RETURN_IN_TRANSIT.value,
        DeliveryStopStatus.RETURN_INITIATED.value,
    }:
        return "RETURNING_TO_DEPOT", "Returning to depot"
    if order_status in {OrderStatus.AT_WAREHOUSE.value, OrderStatus.SORTING_IN_PROGRESS.value}:
        return "SORTING_BACKLOG", "Sorting backlog"
    if stop_status in {DeliveryStopStatus.PICKUP_SCHEDULED.value, DeliveryStopStatus.ENROUTE_PICKUP.value}:
        return "HIGH_PICKUP_VOLUME", "High pickup volume"
    if scheduled_for is not None and scheduled_for < today and stop_status == DeliveryStopStatus.OUT_FOR_DELIVERY.value:
        return "DELIVERY_DELAYED", "Traffic delay"
    if stop_status == DeliveryStopStatus.OUT_FOR_DELIVERY.value:
        return "OUT_FOR_DELIVERY", "Out for delivery"
    if stop_status in {DeliveryStopStatus.PICKUP_SCHEDULED.value, DeliveryStopStatus.ENROUTE_PICKUP.value}:
        return "OUT_FOR_PICKUP", "Out for pickup"
    return "OPERATIONAL_REVIEW", "Requires operational review"


def _format_remediation(
    *,
    automation_message: str | None,
    latest_event_to: str | None,
    scheduled_for: date | None,
    today: date,
) -> tuple[str, bool]:
    if automation_message:
        return automation_message.strip(), True
    if latest_event_to and "RESCHEDUL" in latest_event_to.upper():
        return "Auto rescheduled", True
    if scheduled_for is not None and scheduled_for > today:
        delta_days = (scheduled_for - today).days
        if delta_days > 0:
            label = "Rescheduled for 2 days" if delta_days == 2 else f"Rescheduled for {delta_days} days"
            return label, True
    if latest_event_to and latest_event_to.upper() in {"ASSIGNED", "EN_ROUTE", "LOADED_FOR_DELIVERY"}:
        return "Route updated", True
    return "No Action", False


def _format_driver_name(raw: dict[str, Any]) -> str | None:
    first = (raw.get("driver_first_name") or "").strip()
    last = (raw.get("driver_last_name") or "").strip()
    if first or last:
        if first and last:
            return f"{first[0]}. {last}"
        return first or last
    code = raw.get("driver_code")
    return str(code) if code else None


def _deadline_urgency(scheduled_for: date | None, *, today: date) -> str:
    if scheduled_for is None:
        return "none"
    if scheduled_for <= today:
        return "critical"
    if scheduled_for <= today + timedelta(days=2):
        return "warning"
    return "normal"
