"""Driver enums."""

import enum


class DriverAccountStatus(enum.StrEnum):
    """Lifecycle / account status for a driver profile."""

    DRAFT = "DRAFT"
    PENDING_ACTIVATION = "PENDING_ACTIVATION"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"


class DriverLiveStatus(enum.StrEnum):
    """Live operational status for dispatch / tracking views."""

    ON_ROUTE = "ON_ROUTE"
    ON_BREAK = "ON_BREAK"
    TIME_OFF = "TIME_OFF"
    RETURNING = "RETURNING"
    OFFLINE = "OFFLINE"
    NON_WORKING_DAY = "NON_WORKING_DAY"


class DriverType(enum.StrEnum):
    """Type of driver contract."""

    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


class DriverDocumentKind(enum.StrEnum):
    """Kinds of driver documents stored in the system.

    Title is derived from the enum value with spaces instead of underscores,
    except for CUSTOM which requires a custom title.
    """

    DRIVING_LICENCE = "DRIVING_LICENCE"
    CUSTOM = "CUSTOM"

    def to_display_title(self) -> str:
        """Return the canonical display title (enum value with spaces). For CUSTOM, returns empty string; caller must provide title."""
        if self is DriverDocumentKind.CUSTOM:
            return ""
        return self.value.replace("_", " ")


class DriverCapacity(enum.StrEnum):
    """Vehicle capacity for drivers."""

    VAN = "VAN"
    TRUCK = "TRUCK"


class TimeOffType(enum.StrEnum):
    """Types of planned time off (matches driver app leave types)."""

    ANNUAL_LEAVE = "ANNUAL_LEAVE"
    SICK_LEAVE = "SICK_LEAVE"
    MEDICAL_APPOINTMENT = "MEDICAL_APPOINTMENT"
    FAMILY_PARENTAL_LEAVE = "FAMILY_PARENTAL_LEAVE"
    PATERNITY_LEAVE = "PATERNITY_LEAVE"
    MATERNITY_LEAVE = "MATERNITY_LEAVE"
    ADOPTION_LEAVE = "ADOPTION_LEAVE"
    DISCRETIONARY_SPECIAL_LEAVE = "DISCRETIONARY_SPECIAL_LEAVE"
    EMERGENCY_LEAVE = "EMERGENCY_LEAVE"
    CIVIC_STATUTORY_DUTIES = "CIVIC_STATUTORY_DUTIES"


class ShiftType(enum.StrEnum):
    """Shift templates for scheduling."""

    DAY = "DAY"
    NIGHT = "NIGHT"
    SWING = "SWING"


class ShiftStatus(enum.StrEnum):
    """Status of a planned shift."""

    PLANNED = "PLANNED"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ShiftOrigin(enum.StrEnum):
    """How a driver shift row was created."""

    WEEKLY_TEMPLATE = "WEEKLY_TEMPLATE"
    MANUAL = "MANUAL"


class DriverDocumentStatus(enum.StrEnum):
    """Computed status for driver compliance documents."""

    VALID = "VALID"
    EXPIRING_SOON = "EXPIRING_SOON"
    EXPIRED = "EXPIRED"


class TrafficViolationStatus(enum.StrEnum):
    """Status of a driver traffic violation (ticket)."""

    UNPAID = "UNPAID"
    PAID = "PAID"


class TrafficViolationType(enum.StrEnum):
    """Type of a driver traffic violation (ticket)."""

    SPEEDING = "SPEEDING"
    RED_LIGHT = "RED_LIGHT"
    PARKING = "PARKING"
    BUS_LANE = "BUS_LANE"


class DriverMapPreference(enum.StrEnum):
    """Preferred map app for navigation handoff in driver mobile."""

    GOOGLE_MAPS = "GOOGLE_MAPS"
    WAZE = "WAZE"
    APPLE_MAPS = "APPLE_MAPS"


class CalendarEventSource(enum.StrEnum):
    """Source buckets for schedule-availability calendar entries."""

    SHIFT = "SHIFT"
    TIME_OFF = "TIME_OFF"
    HOLIDAY = "HOLIDAY"
    ROUTE = "ROUTE"


class DriverStopPackageFinalStatus(enum.StrEnum):
    """Body for PATCH …/packages/{id}/status (not used on PICKUP — scan + complete only).

    **DELIVERY** — ``DELIVERED_TO_CUSTOMER``, ``LEFT_AT_SAFE_PLACE``, ``CUSTOMER_NOT_HOME``, ``REFUSED_BY_CUSTOMER``.

    **RETURN** — ``RETURNED_TO_SENDER``, ``SENDER_NOT_HOME``, ``DISPOSED`` (operations only).

    Stored package rows still use ``RETURNED`` / ``CUSTOMER_NOT_HOME``; the API maps the return labels above.
    """

    DELIVERED_TO_CUSTOMER = "DELIVERED_TO_CUSTOMER"
    LEFT_AT_SAFE_PLACE = "LEFT_AT_SAFE_PLACE"
    CUSTOMER_NOT_HOME = "CUSTOMER_NOT_HOME"
    REFUSED_BY_CUSTOMER = "REFUSED_BY_CUSTOMER"
    RETURNED_TO_SENDER = "RETURNED_TO_SENDER"
    SENDER_NOT_HOME = "SENDER_NOT_HOME"
    DISPOSED = "DISPOSED"


class DriverMissingPackageReasonCode(enum.StrEnum):
    """Allowed reasons when reporting a package as missing."""

    NOT_IN_MY_VEHICLE = "NOT_IN_MY_VEHICLE"
    POSSIBLY_LEFT_AT_PREVIOUS_STOP = "POSSIBLY_LEFT_AT_PREVIOUS_STOP"
    WAREHOUSE_LOADING_ERROR = "WAREHOUSE_LOADING_ERROR"
    BARCODE_DAMAGED_CANNOT_SCAN = "BARCODE_DAMAGED_CANNOT_SCAN"
    OTHER = "OTHER"
