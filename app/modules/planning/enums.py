"""Route planning enums."""

import enum


class RoutePlanStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    OPTIMIZING = "OPTIMIZING"
    # Transitional legacy value observed in production rows; retained for read-compatibility
    # until DB backfill migration normalizes to READY.
    PUBLISHED = "PUBLISHED"
    READY = "READY"
    LOCKED = "LOCKED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"


class RouteStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    ASSIGNED = "ASSIGNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"


class RouteType(enum.StrEnum):
    PICKUP = "PICKUP"
    DELIVERY = "DELIVERY"


class RouteStopStatus(enum.StrEnum):
    PENDING = "PENDING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    READY = "READY"
    ASSIGNED = "ASSIGNED"
    EN_ROUTE = "EN_ROUTE"
    ARRIVED = "ARRIVED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RouteStopFlowType(enum.StrEnum):
    """Operational leg for this row on the route (per stop, not the route-level ``Route.route_type``)."""

    PICKUP = "PICKUP"
    DELIVERY = "DELIVERY"
    RETURN = "RETURN"


class StopAssignmentSource(enum.StrEnum):
    INCREMENTAL = "INCREMENTAL"
    OPTIMIZER = "OPTIMIZER"
    MANUAL = "MANUAL"
