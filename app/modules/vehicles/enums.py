import enum


class VehicleType(enum.StrEnum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


class VehicleStatus(enum.StrEnum):
    """Lifecycle status — set by the server, not the user."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"


class VehicleAvailability(enum.StrEnum):
    """Operational availability — selected by the user."""

    ACTIVE = "ACTIVE"
    UNAVAILABLE = "UNAVAILABLE"
    IN_MAINTENANCE = "IN_MAINTENANCE"


class FuelType(enum.StrEnum):
    """Legacy seed codes retained for historical migration compatibility only."""

    DIESEL = "DIESEL"
    PETROL = "PETROL"
    ELECTRIC = "ELECTRIC"


class MaintenanceType(enum.StrEnum):
    """Legacy seed codes retained for historical migration compatibility only."""

    OIL_CHANGE = "OIL_CHANGE"
    REPAIR = "REPAIR"
    MOT = "MOT"
    TYRE_REPLACEMENT = "TYRE_REPLACEMENT"
    INSPECTION = "INSPECTION"
    BODYWORK = "BODYWORK"


class ServiceType(enum.StrEnum):
    """Legacy seed codes retained for historical migration compatibility only."""

    INTERIM_SERVICE = "INTERIM_SERVICE"
    FULL_SERVICE = "FULL_SERVICE"
    MAJOR_SERVICE = "MAJOR_SERVICE"
    MANUFACTURER_SERVICE = "MANUFACTURER_SERVICE"


class LiveStatus(enum.StrEnum):
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    OUT_FOR_PICKUP = "OUT_FOR_PICKUP"
    IDLE = "IDLE"
    COMPLETED = "COMPLETED"


class MaintenanceProviderType(enum.StrEnum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


class DefectSeverity(enum.StrEnum):
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"


class DefectStatus(enum.StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"


class ServiceStatus(enum.StrEnum):
    COMPLETED = "COMPLETED"
    DUE_SOON = "DUE_SOON"
    OVERDUE = "OVERDUE"
    SCHEDULED = "SCHEDULED"


class DocumentType(enum.StrEnum):
    MOT = "MOT"
    TAX = "TAX"
    INSURANCE = "INSURANCE"
    OTHER = "OTHER"


class ScheduleEventType(enum.StrEnum):
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    OUT_FOR_PICKUP = "OUT_FOR_PICKUP"
    COMPLETED = "COMPLETED"
    COMPLETED_DELIVERY = "COMPLETED_DELIVERY"
    COMPLETED_PICKUP = "COMPLETED_PICKUP"
    MAINTENANCE = "MAINTENANCE"
    UNAVAILABLE = "UNAVAILABLE"
    AVAILABLE = "AVAILABLE"


class ScheduleCalendarFilterKind(enum.StrEnum):
    DELIVERY_ROUTE = "DELIVERY_ROUTE"
    PICKUP_ROUTE = "PICKUP_ROUTE"
    MAINTENANCE = "MAINTENANCE"
    UNAVAILABLE = "UNAVAILABLE"


class ScheduleEntrySource(enum.StrEnum):
    MAINTENANCE = "MAINTENANCE"
    AVAILABILITY = "AVAILABILITY"
    ROUTE = "ROUTE"


class MotFilterStatus(enum.StrEnum):
    """Dashboard MOT filter: derived from mot_expiry vs today."""

    VALID = "VALID"
    EXPIRING_SOON = "EXPIRING_SOON"
    EXPIRED = "EXPIRED"
    MISSING = "MISSING"


class TaxFilterStatus(enum.StrEnum):
    """Dashboard Tax filter: derived from tax_due_date vs today."""

    PAID = "PAID"
    DUE_SOON = "DUE_SOON"
    OVERDUE = "OVERDUE"
    MISSING = "MISSING"


class ServiceBadgeStatus(enum.StrEnum):
    VALID = "VALID"
    DUE_SOON = "DUE_SOON"
    OVERDUE = "OVERDUE"
    UNKNOWN = "UNKNOWN"


class CardDisplayUnit(enum.StrEnum):
    MILES = "MILES"
    DAYS = "DAYS"
    MPG = "MPG"
    UNKNOWN = "UNKNOWN"
