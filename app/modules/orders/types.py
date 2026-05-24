from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(slots=True)
class OrderStatusCounts:
    total: int = 0
    pickups_on_route: int = 0
    delivered: int = 0
    cancelled: int = 0
    failed: int = 0
    returned: int = 0


@dataclass(slots=True)
class OrderSummaryResult:
    period_from: date | None
    period_to: date | None
    previous_period_from: date | None
    previous_period_to: date | None
    comparison_label: str
    current: OrderStatusCounts
    previous: OrderStatusCounts


@dataclass(slots=True)
class FailedDeliveryCounts:
    total: int = 0
    missing: int = 0
    damaged: int = 0
    cancelled: int = 0
    customer_not_home: int = 0
    refused: int = 0
    disposed: int = 0


@dataclass(slots=True)
class ReturnsCounts:
    total: int = 0
    in_transit: int = 0
    disposed: int = 0
    returned: int = 0
    initiated: int = 0
    avg_resolution_days: float | None = None


@dataclass(slots=True)
class StatusEventRecord:
    id: str
    created_at: datetime
    from_status: str | None
    to_status: str
    actor_user_id: str | None


@dataclass(slots=True)
class FailedPackageRow:
    package_pk: str
    package_id: str
    status: str
    reason: str | None = None
    status_events: list[StatusEventRecord] = field(default_factory=list)


@dataclass(slots=True)
class FailedDeliveryStopRow:
    delivery_stop_id: str
    tracking_id: str | None
    postcode: str | None
    order_id: str
    order_reference: str
    stop_status: str
    attempt_number: int
    max_attempts: int
    previous_attempt_at: datetime | None
    next_attempt_at: datetime | None
    stop_status_events: list[StatusEventRecord] = field(default_factory=list)
    packages: list[FailedPackageRow] = field(default_factory=list)


@dataclass(slots=True)
class ReturnPackageRow:
    package_pk: str
    package_id: str
    status: str
    return_reason: str | None = None
    initiated_at: datetime | None = None
    status_events: list[StatusEventRecord] = field(default_factory=list)


@dataclass(slots=True)
class ReturnStopRow:
    delivery_stop_id: str
    tracking_id: str | None
    postcode: str | None
    order_id: str
    order_reference: str
    initiated_at: datetime | None
    stop_status: str = ""
    attempt_number: int = 0
    max_attempts: int = 3
    stop_status_events: list[StatusEventRecord] = field(default_factory=list)
    packages: list[ReturnPackageRow] = field(default_factory=list)


@dataclass(slots=True)
class FailedDeliverySummaryResult:
    period_from: date | None
    period_to: date | None
    previous_period_from: date | None
    previous_period_to: date | None
    comparison_label: str
    current: FailedDeliveryCounts
    previous: FailedDeliveryCounts


@dataclass(slots=True)
class ReturnsSummaryResult:
    period_from: date | None
    period_to: date | None
    previous_period_from: date | None
    previous_period_to: date | None
    comparison_label: str
    current: ReturnsCounts
    previous: ReturnsCounts

