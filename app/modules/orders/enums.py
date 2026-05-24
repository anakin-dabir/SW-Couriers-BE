import enum

from app.common.enums.delivery import DeliveryServiceTier

ServiceTier = DeliveryServiceTier


class ClientTypeEnum(enum.StrEnum):
    B2B = "B2B"
    B2C = "B2C"


class OrderStatus(enum.StrEnum):
    PENDING_PICKUP = "PENDING_PICKUP"
    PICKUP_SCHEDULED = "PICKUP_SCHEDULED"
    ENROUTE_PICKUP = "ENROUTE_PICKUP"
    ENROUTE_WAREHOUSE = "ENROUTE_WAREHOUSE"
    AT_WAREHOUSE = "AT_WAREHOUSE"
    SORTING_IN_PROGRESS = "SORTING_IN_PROGRESS"
    DELIVERY_IN_PROGRESS = "DELIVERY_IN_PROGRESS"
    PARTIALLY_DELIVERED = "PARTIALLY_DELIVERED"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    RETURN_IN_PROGRESS = "RETURN_IN_PROGRESS"
    RETURN_IN_TRANSIT = "RETURN_IN_TRANSIT"
    RETURNED = "RETURNED"
    CANCELLED = "CANCELLED"


class DeliveryStopStatus(enum.StrEnum):
    PENDING_PICKUP = "PENDING_PICKUP"
    PICKUP_SCHEDULED = "PICKUP_SCHEDULED"
    ENROUTE_PICKUP = "ENROUTE_PICKUP"
    ENROUTE_WAREHOUSE = "ENROUTE_WAREHOUSE"
    AT_WAREHOUSE = "AT_WAREHOUSE"
    SORTING_IN_PROGRESS = "SORTING_IN_PROGRESS"
    DELIVERY_SCHEDULED = "DELIVERY_SCHEDULED"
    LOADED_FOR_DELIVERY = "LOADED_FOR_DELIVERY"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    PARTIALLY_DELIVERED = "PARTIALLY_DELIVERED"
    DELIVERY_ATTEMPT_1_FAILED = "DELIVERY_ATTEMPT_1_FAILED"
    DELIVERY_ATTEMPT_2_FAILED = "DELIVERY_ATTEMPT_2_FAILED"
    DELIVERY_ATTEMPT_3_FAILED = "DELIVERY_ATTEMPT_3_FAILED"
    MIXED = "MIXED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RETURN_INITIATED = "RETURN_INITIATED"
    RETURN_IN_TRANSIT = "RETURN_IN_TRANSIT"
    RETURNED = "RETURNED"
    DISPOSED = "DISPOSED"


class PackageStatus(enum.StrEnum):
    PENDING_PICKUP = "PENDING_PICKUP"
    PICKUP_SCHEDULED = "PICKUP_SCHEDULED"
    ENROUTE_PICKUP = "ENROUTE_PICKUP"
    ENROUTE_WAREHOUSE = "ENROUTE_WAREHOUSE"
    AT_WAREHOUSE = "AT_WAREHOUSE"
    SORTING_IN_PROGRESS = "SORTING_IN_PROGRESS"
    DELIVERY_SCHEDULED = "DELIVERY_SCHEDULED"
    LOADED_FOR_DELIVERY = "LOADED_FOR_DELIVERY"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED_TO_CUSTOMER = "DELIVERED_TO_CUSTOMER"
    CUSTOMER_NOT_HOME = "CUSTOMER_NOT_HOME"
    REFUSED_BY_CUSTOMER = "REFUSED_BY_CUSTOMER"
    MISSING = "MISSING"
    DAMAGED = "DAMAGED"
    LEFT_AT_SAFE_PLACE = "LEFT_AT_SAFE_PLACE"
    RETURN_INITIATED = "RETURN_INITIATED"
    RETURN_IN_TRANSIT = "RETURN_IN_TRANSIT"
    RETURNED = "RETURNED"
    CANCELLED = "CANCELLED"
    DISPOSED = "DISPOSED"


PACKAGE_STOP_DELIVERY_OUTCOME_STATUSES: frozenset[PackageStatus] = frozenset(
    {
        PackageStatus.DELIVERED_TO_CUSTOMER,
        PackageStatus.LEFT_AT_SAFE_PLACE,
        PackageStatus.CUSTOMER_NOT_HOME,
        PackageStatus.REFUSED_BY_CUSTOMER,
        PackageStatus.MISSING,
        PackageStatus.DAMAGED,
    }
)

# Pickup leg: packages still awaiting collection scan at customer / pickup stop.
PACKAGE_PRE_PICKUP_FOR_SCAN_STATUSES: frozenset[PackageStatus] = frozenset(
    {
        PackageStatus.PENDING_PICKUP,
        PackageStatus.PICKUP_SCHEDULED,
        PackageStatus.ENROUTE_PICKUP,
    }
)

# Return leg: terminal outcomes after the driver records a result (scan then disposition).
# CUSTOMER_NOT_HOME = "sender not home" / could not hand back — no stop-level POD required.
PACKAGE_STOP_RETURN_HUB_COMPLETE_STATUSES: frozenset[PackageStatus] = frozenset(
    {
        PackageStatus.RETURNED,
        PackageStatus.DISPOSED,
        PackageStatus.CANCELLED,
        PackageStatus.CUSTOMER_NOT_HOME,
    }
)

# Return leg: these package statuses require ≥1 POD photo on the stop before completion.
PACKAGE_RETURN_FLOW_REQUIRES_STOP_POD_STATUSES: frozenset[PackageStatus] = frozenset(
    {
        PackageStatus.RETURNED,
    }
)

# Driver PATCH …/packages/{id}/status maps to these ``PackageStatus`` values after leg-specific parsing
# (delivery: UI strings match stored values; return: ``RETURNED_TO_SENDER``→RETURNED, ``SENDER_NOT_HOME``→CUSTOMER_NOT_HOME).
PACKAGE_DRIVER_PATCH_DELIVERY_STATUSES: frozenset[PackageStatus] = frozenset(
    {
        PackageStatus.DELIVERED_TO_CUSTOMER,
        PackageStatus.LEFT_AT_SAFE_PLACE,
        PackageStatus.CUSTOMER_NOT_HOME,
        PackageStatus.REFUSED_BY_CUSTOMER,
    }
)

PACKAGE_DRIVER_PATCH_RETURN_STATUSES: frozenset[PackageStatus] = frozenset(
    {
        PackageStatus.RETURNED,
        PackageStatus.DISPOSED,
        PackageStatus.CUSTOMER_NOT_HOME,
    }
)


FAILED_PACKAGE_STATUSES: frozenset[PackageStatus] = frozenset(
    {
        PackageStatus.CUSTOMER_NOT_HOME,
        PackageStatus.REFUSED_BY_CUSTOMER,
        PackageStatus.MISSING,
        PackageStatus.DAMAGED,
        PackageStatus.CANCELLED,
    }
)


RETURN_PACKAGE_STATUSES: frozenset[PackageStatus] = frozenset(
    {
        PackageStatus.RETURN_INITIATED,
        PackageStatus.RETURN_IN_TRANSIT,
        PackageStatus.RETURNED,
        PackageStatus.DISPOSED,
    }
)


RETURN_IN_TRANSIT_STATUSES: frozenset[PackageStatus] = frozenset(
    {
        PackageStatus.RETURN_INITIATED,
        PackageStatus.RETURN_IN_TRANSIT,
    }
)


PICKUP_ON_ROUTE_ORDER_STATUSES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.PICKUP_SCHEDULED,
        OrderStatus.ENROUTE_PICKUP,
    }
)


RESCHEDULABLE_PACKAGE_STATUSES: frozenset[PackageStatus] = frozenset(
    {
        PackageStatus.CUSTOMER_NOT_HOME,
    }
)


RETURNABLE_PACKAGE_STATUSES: frozenset[PackageStatus] = frozenset(
    {
        PackageStatus.CUSTOMER_NOT_HOME,
        PackageStatus.REFUSED_BY_CUSTOMER,
        PackageStatus.MISSING,
        PackageStatus.DAMAGED,
    }
)


RESOLVABLE_RETURN_PACKAGE_STATUSES: frozenset[PackageStatus] = frozenset(
    {
        PackageStatus.RETURN_INITIATED,
    }
)


PACKAGE_STATUSES_BLOCKING_CANCELLATION: frozenset[PackageStatus] = frozenset(
    {
        PackageStatus.DELIVERED_TO_CUSTOMER,
        PackageStatus.LEFT_AT_SAFE_PLACE,
        PackageStatus.RETURNED,
        PackageStatus.DISPOSED,
        PackageStatus.RETURN_IN_TRANSIT,
        PackageStatus.RETURN_INITIATED,
    }
)


MAX_DELIVERY_ATTEMPTS: int = 3
MAX_RETURN_EVIDENCE_IMAGES: int = 5


def attempt_number_from_stop_status(status: DeliveryStopStatus | None) -> int:
    if status == DeliveryStopStatus.DELIVERY_ATTEMPT_1_FAILED:
        return 1
    if status == DeliveryStopStatus.DELIVERY_ATTEMPT_2_FAILED:
        return 2
    if status == DeliveryStopStatus.DELIVERY_ATTEMPT_3_FAILED:
        return 3
    return 0


class OrderDraftStatus(enum.StrEnum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"


class SummaryPeriodPreset(enum.StrEnum):
    TODAY = "TODAY"
    YESTERDAY = "YESTERDAY"
    LAST_7_DAYS = "LAST_7_DAYS"
    LAST_WEEK = "LAST_WEEK"
    LAST_30_DAYS = "LAST_30_DAYS"
    LAST_MONTH = "LAST_MONTH"


class ReturnResolution(enum.StrEnum):
    RETURN_TO_SENDER = "RETURN_TO_SENDER"
    DISPOSE = "DISPOSE"


class DisposalReason(enum.StrEnum):
    DAMAGED_PARCEL = "DAMAGED_PARCEL"
    ABANDONED = "ABANDONED"
    OPERATIONAL_INSTRUCTION = "OPERATIONAL_INSTRUCTION"
    OTHER = "OTHER"


class StopNoteType(enum.StrEnum):
    """Persisted ``stop_notes.note_type`` values."""

    ADMIN = "ADMIN"
    CUSTOMER = "CUSTOMER"
    PACKAGE_ISSUE_NOTE = "PACKAGE_ISSUE_NOTE"


STOP_NOTE_PACKAGE_IDS_MAX = 50
