import enum


class NotificationType(enum.StrEnum):
    ADMIN_INTERNAL = "ADMIN_INTERNAL"
    B2B_CUSTOMER = "B2B_CUSTOMER"
    RECIPIENT = "RECIPIENT"
    DRIVER = "DRIVER"


class NotificationChannel(enum.StrEnum):
    IN_APP = "IN_APP"
    SMS = "SMS"
    EMAIL = "EMAIL"
    PUSH = "PUSH"


class TemplateChannel(enum.StrEnum):
    """Channels a preference screen exposes as editable templates."""

    EMAIL = "EMAIL"
    SMS = "SMS"


class PreferenceScope(enum.StrEnum):
    """Caller-facing scope, matching the API route prefix.

    Dispatch to the underlying storage layer is based on ``(scope, notification_type)``:

    - ``ADMIN`` (/admin) + ``ADMIN_INTERNAL``                → user preferences (admin's own)
    - ``ADMIN`` (/) + ``B2B_CUSTOMER`` / ``RECIPIENT``  → system defaults (global)
    - ``ORGANIZATION`` + allowed type               → org preferences
    - ``B2B_DASHBOARD`` + ``B2B_CUSTOMER``          → user preferences (b2b contact's own)
    """

    ADMIN = "ADMIN"
    ORGANIZATION = "ORGANIZATION"
    B2B_DASHBOARD = "B2B_DASHBOARD"


class PreferenceStream(enum.StrEnum):
    """UI tab a set of preferences belongs to.

    Maps to ``NotificationType``:

    - ``ADMIN_INTERNAL``  — admin user personal inbox preferences
    - ``B2B_CUSTOMER``    — B2B user / org preferences for their own inbox
    - ``RECIPIENT``       — end-customer (no account) delivery notifications
    """

    ADMIN_INTERNAL = "ADMIN_INTERNAL"
    B2B_CUSTOMER = "B2B_CUSTOMER"
    RECIPIENT = "RECIPIENT"


CHANNELS_BY_TYPE: dict[NotificationType, tuple[NotificationChannel, ...]] = {
    NotificationType.ADMIN_INTERNAL: (NotificationChannel.EMAIL, NotificationChannel.SMS),
    NotificationType.B2B_CUSTOMER: (NotificationChannel.EMAIL, NotificationChannel.SMS),
    NotificationType.RECIPIENT: (NotificationChannel.EMAIL, NotificationChannel.SMS),
    NotificationType.DRIVER: (NotificationChannel.PUSH, NotificationChannel.IN_APP),
}


class NotificationStatus(enum.StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class NotificationCategory(enum.StrEnum):
    SHIPMENT = "SHIPMENT"
    BILLING = "BILLING"
    ADMIN_ORDERS = "ADMIN_ORDERS"
    ADMIN_DRIVER_ISSUES = "ADMIN_DRIVER_ISSUES"
    ADMIN_VEHICLE_ISSUES = "ADMIN_VEHICLE_ISSUES"
    ADMIN_ACCOUNT = "ADMIN_ACCOUNT"
    ADMIN_CREDIT = "ADMIN_CREDIT"
    ADMIN_QUICKBOOKS = "ADMIN_QUICKBOOKS"
    ADMIN_SYSTEM = "ADMIN_SYSTEM"
    RECIPIENT_DELIVERY = "RECIPIENT_DELIVERY"


class NotificationEvent(enum.StrEnum):
    ADMIN_NEW_ORDER_CREATED = "ADMIN_NEW_ORDER_CREATED"
    ADMIN_ORDER_DELIVERED_SUCCESSFULLY = "ADMIN_ORDER_DELIVERED_SUCCESSFULLY"
    ADMIN_ORDER_DELIVERY_FAILED = "ADMIN_ORDER_DELIVERY_FAILED"
    ADMIN_ORDER_CANCELLED = "ADMIN_ORDER_CANCELLED"
    ADMIN_PACKAGE_MISSING_REPORTED = "ADMIN_PACKAGE_MISSING_REPORTED"
    ADMIN_PACKAGE_DAMAGED_REPORTED = "ADMIN_PACKAGE_DAMAGED_REPORTED"
    ADMIN_REPORTED_DEFECTS = "ADMIN_REPORTED_DEFECTS"
    ADMIN_VEHICLE_BREAKDOWN_REPORTED = "ADMIN_VEHICLE_BREAKDOWN_REPORTED"
    ADMIN_VEHICLE_MAINTENANCE_DUE = "ADMIN_VEHICLE_MAINTENANCE_DUE"
    ADMIN_DRIVER_ACCOUNT_SUSPENDED = "ADMIN_DRIVER_ACCOUNT_SUSPENDED"
    ADMIN_DRIVER_ACCOUNT_DELETED = "ADMIN_DRIVER_ACCOUNT_DELETED"
    ADMIN_CLIENT_ACCOUNT_SUSPENDED = "ADMIN_CLIENT_ACCOUNT_SUSPENDED"
    ADMIN_CLIENT_ACCOUNT_DELETED = "ADMIN_CLIENT_ACCOUNT_DELETED"
    ADMIN_QUICKBOOKS_CONNECTION_FAILURE = "ADMIN_QUICKBOOKS_CONNECTION_FAILURE"
    ADMIN_DATA_SYNC_FAILURE = "ADMIN_DATA_SYNC_FAILURE"
    ADMIN_HIGH_NUMBER_OF_DELAYED_ORDERS = "ADMIN_HIGH_NUMBER_OF_DELAYED_ORDERS"
    ADMIN_ACTIVATION_LINK_REQUESTED = "ADMIN_ACTIVATION_LINK_REQUESTED"

    BOOKING_CONFIRMATION = "BOOKING_CONFIRMATION"
    PICKUP_SCHEDULED = "PICKUP_SCHEDULED"
    PICKUP_ON_THE_WAY = "PICKUP_ON_THE_WAY"
    PICKUP_COMPLETED = "PICKUP_COMPLETED"
    IN_TRANSIT_TO_WAREHOUSE = "IN_TRANSIT_TO_WAREHOUSE"
    PACKAGE_IN_WAREHOUSE = "PACKAGE_IN_WAREHOUSE"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERY_SUCCESSFUL = "DELIVERY_SUCCESSFUL"
    DELIVERY_PARTIAL = "DELIVERY_PARTIAL"
    DELIVERY_FAILED_ATTEMPT = "DELIVERY_FAILED_ATTEMPT"
    DELIVERY_FAILED_FINAL = "DELIVERY_FAILED_FINAL"
    RETURN_INITIATED = "RETURN_INITIATED"
    RETURN_SCHEDULED = "RETURN_SCHEDULED"
    RETURN_IN_TRANSIT = "RETURN_IN_TRANSIT"
    RETURN_COMPLETED = "RETURN_COMPLETED"
    RETURNED_TO_SENDER = "RETURNED_TO_SENDER"
    BOOKING_DISPOSED = "BOOKING_DISPOSED"

    INVOICE_GENERATED = "INVOICE_GENERATED"
    INVOICE_OVERDUE = "INVOICE_OVERDUE"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    CREDIT_UTILISATION_MONITORING_WARNING = "CREDIT_UTILISATION_MONITORING_WARNING"
    CREDIT_UTILISATION_MONITORING_CRITICAL = "CREDIT_UTILISATION_MONITORING_CRITICAL"

    RECIPIENT_PENDING_PICKUP = "RECIPIENT_PENDING_PICKUP"
    RECIPIENT_PICKUP_SCHEDULED = "RECIPIENT_PICKUP_SCHEDULED"
    RECIPIENT_AT_WAREHOUSE = "RECIPIENT_AT_WAREHOUSE"
    RECIPIENT_DELIVERY_SCHEDULED = "RECIPIENT_DELIVERY_SCHEDULED"
    RECIPIENT_OUT_FOR_DELIVERY = "RECIPIENT_OUT_FOR_DELIVERY"
    RECIPIENT_PARTIALLY_DELIVERED = "RECIPIENT_PARTIALLY_DELIVERED"
    RECIPIENT_DELIVERY_FAILED_ATTEMPT = "RECIPIENT_DELIVERY_FAILED_ATTEMPT"
    RECIPIENT_DELIVERY_FAILED_FINAL = "RECIPIENT_DELIVERY_FAILED_FINAL"
    RECIPIENT_DELIVERED = "RECIPIENT_DELIVERED"
    RECIPIENT_CANCELLED = "RECIPIENT_CANCELLED"

    DRIVER_VEHICLE_SERVICE_DUE = "DRIVER_VEHICLE_SERVICE_DUE"
    DRIVER_WORK_SCHEDULE_UPDATED = "DRIVER_WORK_SCHEDULE_UPDATED"


_A = frozenset({NotificationType.ADMIN_INTERNAL})
_B = frozenset({NotificationType.B2B_CUSTOMER})
_R = frozenset({NotificationType.RECIPIENT})
_D = frozenset({NotificationType.DRIVER})

EVENT_NOTIFICATION_STREAMS: dict[NotificationEvent, frozenset[NotificationType]] = {
    NotificationEvent.ADMIN_NEW_ORDER_CREATED: _A,
    NotificationEvent.ADMIN_ORDER_DELIVERED_SUCCESSFULLY: _A,
    NotificationEvent.ADMIN_ORDER_DELIVERY_FAILED: _A,
    NotificationEvent.ADMIN_ORDER_CANCELLED: _A,
    NotificationEvent.ADMIN_PACKAGE_MISSING_REPORTED: _A,
    NotificationEvent.ADMIN_PACKAGE_DAMAGED_REPORTED: _A,
    NotificationEvent.ADMIN_REPORTED_DEFECTS: _A,
    NotificationEvent.ADMIN_VEHICLE_BREAKDOWN_REPORTED: _A,
    NotificationEvent.ADMIN_VEHICLE_MAINTENANCE_DUE: _A,
    NotificationEvent.ADMIN_DRIVER_ACCOUNT_SUSPENDED: _A,
    NotificationEvent.ADMIN_DRIVER_ACCOUNT_DELETED: _A,
    NotificationEvent.ADMIN_CLIENT_ACCOUNT_SUSPENDED: _A,
    NotificationEvent.ADMIN_CLIENT_ACCOUNT_DELETED: _A,
    NotificationEvent.ADMIN_QUICKBOOKS_CONNECTION_FAILURE: _A,
    NotificationEvent.ADMIN_DATA_SYNC_FAILURE: _A,
    NotificationEvent.ADMIN_HIGH_NUMBER_OF_DELAYED_ORDERS: _A,
    NotificationEvent.BOOKING_CONFIRMATION: _B,
    NotificationEvent.PICKUP_SCHEDULED: _B,
    NotificationEvent.PICKUP_ON_THE_WAY: _B,
    NotificationEvent.PICKUP_COMPLETED: _B,
    NotificationEvent.IN_TRANSIT_TO_WAREHOUSE: _B,
    NotificationEvent.PACKAGE_IN_WAREHOUSE: _B,
    NotificationEvent.OUT_FOR_DELIVERY: _B,
    NotificationEvent.DELIVERY_SUCCESSFUL: _B,
    NotificationEvent.DELIVERY_PARTIAL: _B,
    NotificationEvent.DELIVERY_FAILED_ATTEMPT: _B,
    NotificationEvent.DELIVERY_FAILED_FINAL: _B,
    NotificationEvent.RETURN_INITIATED: _B,
    NotificationEvent.RETURN_SCHEDULED: _B,
    NotificationEvent.RETURN_IN_TRANSIT: _B,
    NotificationEvent.RETURN_COMPLETED: _B,
    NotificationEvent.RETURNED_TO_SENDER: _B,
    NotificationEvent.BOOKING_DISPOSED: _B,
    NotificationEvent.INVOICE_GENERATED: _B,
    NotificationEvent.INVOICE_OVERDUE: _B,
    NotificationEvent.PAYMENT_RECEIVED: _B,
    NotificationEvent.CREDIT_UTILISATION_MONITORING_WARNING: _B,
    NotificationEvent.CREDIT_UTILISATION_MONITORING_CRITICAL: _B,
    NotificationEvent.RECIPIENT_PENDING_PICKUP: _R,
    NotificationEvent.RECIPIENT_PICKUP_SCHEDULED: _R,
    NotificationEvent.RECIPIENT_AT_WAREHOUSE: _R,
    NotificationEvent.RECIPIENT_DELIVERY_SCHEDULED: _R,
    NotificationEvent.RECIPIENT_OUT_FOR_DELIVERY: _R,
    NotificationEvent.RECIPIENT_PARTIALLY_DELIVERED: _R,
    NotificationEvent.RECIPIENT_DELIVERY_FAILED_ATTEMPT: _R,
    NotificationEvent.RECIPIENT_DELIVERY_FAILED_FINAL: _R,
    NotificationEvent.RECIPIENT_DELIVERED: _R,
    NotificationEvent.RECIPIENT_CANCELLED: _R,
    NotificationEvent.DRIVER_VEHICLE_SERVICE_DUE: _D,
    NotificationEvent.DRIVER_WORK_SCHEDULE_UPDATED: _D,
}


def events_for_notification_type(notification_type: NotificationType) -> tuple[NotificationEvent, ...]:
    """All events applicable to a given notification stream, in enum order."""
    return tuple(e for e in NotificationEvent if notification_type in EVENT_NOTIFICATION_STREAMS.get(e, frozenset()))


EVENT_CATEGORIES: dict[NotificationEvent, NotificationCategory] = {
    NotificationEvent.ADMIN_NEW_ORDER_CREATED: NotificationCategory.ADMIN_ORDERS,
    NotificationEvent.ADMIN_ORDER_DELIVERED_SUCCESSFULLY: NotificationCategory.ADMIN_ORDERS,
    NotificationEvent.ADMIN_ORDER_DELIVERY_FAILED: NotificationCategory.ADMIN_ORDERS,
    NotificationEvent.ADMIN_ORDER_CANCELLED: NotificationCategory.ADMIN_ORDERS,
    NotificationEvent.ADMIN_PACKAGE_MISSING_REPORTED: NotificationCategory.ADMIN_DRIVER_ISSUES,
    NotificationEvent.ADMIN_PACKAGE_DAMAGED_REPORTED: NotificationCategory.ADMIN_DRIVER_ISSUES,
    NotificationEvent.ADMIN_REPORTED_DEFECTS: NotificationCategory.ADMIN_DRIVER_ISSUES,
    NotificationEvent.ADMIN_VEHICLE_BREAKDOWN_REPORTED: NotificationCategory.ADMIN_VEHICLE_ISSUES,
    NotificationEvent.ADMIN_VEHICLE_MAINTENANCE_DUE: NotificationCategory.ADMIN_VEHICLE_ISSUES,
    NotificationEvent.ADMIN_DRIVER_ACCOUNT_SUSPENDED: NotificationCategory.ADMIN_ACCOUNT,
    NotificationEvent.ADMIN_DRIVER_ACCOUNT_DELETED: NotificationCategory.ADMIN_ACCOUNT,
    NotificationEvent.ADMIN_CLIENT_ACCOUNT_SUSPENDED: NotificationCategory.ADMIN_ACCOUNT,
    NotificationEvent.ADMIN_CLIENT_ACCOUNT_DELETED: NotificationCategory.ADMIN_ACCOUNT,
    NotificationEvent.ADMIN_QUICKBOOKS_CONNECTION_FAILURE: NotificationCategory.ADMIN_QUICKBOOKS,
    NotificationEvent.ADMIN_DATA_SYNC_FAILURE: NotificationCategory.ADMIN_SYSTEM,
    NotificationEvent.ADMIN_HIGH_NUMBER_OF_DELAYED_ORDERS: NotificationCategory.ADMIN_SYSTEM,
    NotificationEvent.BOOKING_CONFIRMATION: NotificationCategory.SHIPMENT,
    NotificationEvent.PICKUP_SCHEDULED: NotificationCategory.SHIPMENT,
    NotificationEvent.PICKUP_ON_THE_WAY: NotificationCategory.SHIPMENT,
    NotificationEvent.PICKUP_COMPLETED: NotificationCategory.SHIPMENT,
    NotificationEvent.IN_TRANSIT_TO_WAREHOUSE: NotificationCategory.SHIPMENT,
    NotificationEvent.PACKAGE_IN_WAREHOUSE: NotificationCategory.SHIPMENT,
    NotificationEvent.OUT_FOR_DELIVERY: NotificationCategory.SHIPMENT,
    NotificationEvent.DELIVERY_SUCCESSFUL: NotificationCategory.SHIPMENT,
    NotificationEvent.DELIVERY_PARTIAL: NotificationCategory.SHIPMENT,
    NotificationEvent.DELIVERY_FAILED_ATTEMPT: NotificationCategory.SHIPMENT,
    NotificationEvent.DELIVERY_FAILED_FINAL: NotificationCategory.SHIPMENT,
    NotificationEvent.RETURN_INITIATED: NotificationCategory.SHIPMENT,
    NotificationEvent.RETURN_SCHEDULED: NotificationCategory.SHIPMENT,
    NotificationEvent.RETURN_IN_TRANSIT: NotificationCategory.SHIPMENT,
    NotificationEvent.RETURN_COMPLETED: NotificationCategory.SHIPMENT,
    NotificationEvent.RETURNED_TO_SENDER: NotificationCategory.SHIPMENT,
    NotificationEvent.BOOKING_DISPOSED: NotificationCategory.SHIPMENT,
    NotificationEvent.INVOICE_GENERATED: NotificationCategory.BILLING,
    NotificationEvent.INVOICE_OVERDUE: NotificationCategory.BILLING,
    NotificationEvent.PAYMENT_RECEIVED: NotificationCategory.BILLING,
    NotificationEvent.CREDIT_UTILISATION_MONITORING_WARNING: NotificationCategory.BILLING,
    NotificationEvent.CREDIT_UTILISATION_MONITORING_CRITICAL: NotificationCategory.BILLING,
    NotificationEvent.RECIPIENT_PENDING_PICKUP: NotificationCategory.RECIPIENT_DELIVERY,
    NotificationEvent.RECIPIENT_PICKUP_SCHEDULED: NotificationCategory.RECIPIENT_DELIVERY,
    NotificationEvent.RECIPIENT_AT_WAREHOUSE: NotificationCategory.RECIPIENT_DELIVERY,
    NotificationEvent.RECIPIENT_DELIVERY_SCHEDULED: NotificationCategory.RECIPIENT_DELIVERY,
    NotificationEvent.RECIPIENT_OUT_FOR_DELIVERY: NotificationCategory.RECIPIENT_DELIVERY,
    NotificationEvent.RECIPIENT_PARTIALLY_DELIVERED: NotificationCategory.RECIPIENT_DELIVERY,
    NotificationEvent.RECIPIENT_DELIVERY_FAILED_ATTEMPT: NotificationCategory.RECIPIENT_DELIVERY,
    NotificationEvent.RECIPIENT_DELIVERY_FAILED_FINAL: NotificationCategory.RECIPIENT_DELIVERY,
    NotificationEvent.RECIPIENT_DELIVERED: NotificationCategory.RECIPIENT_DELIVERY,
    NotificationEvent.RECIPIENT_CANCELLED: NotificationCategory.RECIPIENT_DELIVERY,
    NotificationEvent.DRIVER_VEHICLE_SERVICE_DUE: NotificationCategory.ADMIN_VEHICLE_ISSUES,
    NotificationEvent.DRIVER_WORK_SCHEDULE_UPDATED: NotificationCategory.ADMIN_DRIVER_ISSUES,
}


EVENT_DISPLAY_NAMES: dict[NotificationEvent, str] = {
    NotificationEvent.ADMIN_NEW_ORDER_CREATED: "New Order Created",
    NotificationEvent.ADMIN_ORDER_DELIVERED_SUCCESSFULLY: "Order Delivered Successfully",
    NotificationEvent.ADMIN_ORDER_DELIVERY_FAILED: "Order Delivery Failed",
    NotificationEvent.ADMIN_ORDER_CANCELLED: "Order Cancelled",
    NotificationEvent.ADMIN_PACKAGE_MISSING_REPORTED: "Package Missing Reported",
    NotificationEvent.ADMIN_PACKAGE_DAMAGED_REPORTED: "Package Damaged Reported",
    NotificationEvent.ADMIN_REPORTED_DEFECTS: "Reported Defects",
    NotificationEvent.ADMIN_VEHICLE_BREAKDOWN_REPORTED: "Vehicle Breakdown Reported",
    NotificationEvent.ADMIN_VEHICLE_MAINTENANCE_DUE: "Vehicle Maintenance Due",
    NotificationEvent.ADMIN_DRIVER_ACCOUNT_SUSPENDED: "Driver Account Suspended",
    NotificationEvent.ADMIN_DRIVER_ACCOUNT_DELETED: "Driver Account Deleted",
    NotificationEvent.ADMIN_CLIENT_ACCOUNT_SUSPENDED: "Client Account Suspended",
    NotificationEvent.ADMIN_CLIENT_ACCOUNT_DELETED: "Client Account Deleted",
    NotificationEvent.ADMIN_QUICKBOOKS_CONNECTION_FAILURE: "QuickBooks Connection Failure",
    NotificationEvent.ADMIN_DATA_SYNC_FAILURE: "Data Sync Failure",
    NotificationEvent.ADMIN_HIGH_NUMBER_OF_DELAYED_ORDERS: "High Number of Delayed Orders",
    NotificationEvent.BOOKING_CONFIRMATION: "Booking Created",
    NotificationEvent.PICKUP_SCHEDULED: "Pickup Scheduled",
    NotificationEvent.PICKUP_ON_THE_WAY: "Pickup On The Way",
    NotificationEvent.PICKUP_COMPLETED: "Pickup Completed",
    NotificationEvent.IN_TRANSIT_TO_WAREHOUSE: "Booking in Transit to Warehouse",
    NotificationEvent.PACKAGE_IN_WAREHOUSE: "Packages Arrived at Warehouse",
    NotificationEvent.OUT_FOR_DELIVERY: "Packages Out for Delivery",
    NotificationEvent.DELIVERY_SUCCESSFUL: "Packages Delivered",
    NotificationEvent.DELIVERY_PARTIAL: "Packages Partially Delivered",
    NotificationEvent.DELIVERY_FAILED_ATTEMPT: "Delivery Failed (Per Attempt)",
    NotificationEvent.DELIVERY_FAILED_FINAL: "Delivery Failed (Final — All Attempts Exhausted)",
    NotificationEvent.RETURN_INITIATED: "Return Initiated",
    NotificationEvent.RETURN_SCHEDULED: "Return Scheduled",
    NotificationEvent.RETURN_IN_TRANSIT: "Return in Transit",
    NotificationEvent.RETURN_COMPLETED: "Return Completed",
    NotificationEvent.RETURNED_TO_SENDER: "Returned to Sender",
    NotificationEvent.BOOKING_DISPOSED: "Booking Disposed",
    NotificationEvent.INVOICE_GENERATED: "Invoice Generated",
    NotificationEvent.INVOICE_OVERDUE: "Invoice Overdue",
    NotificationEvent.PAYMENT_RECEIVED: "Payment Received",
    NotificationEvent.CREDIT_UTILISATION_MONITORING_WARNING: "Credit Utilisation Warning Threshold",
    NotificationEvent.CREDIT_UTILISATION_MONITORING_CRITICAL: "Credit Utilisation Critical Threshold",
    NotificationEvent.RECIPIENT_PENDING_PICKUP: "Pending Pickup",
    NotificationEvent.RECIPIENT_PICKUP_SCHEDULED: "Pickup Scheduled",
    NotificationEvent.RECIPIENT_AT_WAREHOUSE: "At Warehouse",
    NotificationEvent.RECIPIENT_DELIVERY_SCHEDULED: "Delivery Scheduled",
    NotificationEvent.RECIPIENT_OUT_FOR_DELIVERY: "Out for Delivery",
    NotificationEvent.RECIPIENT_PARTIALLY_DELIVERED: "Partially Delivered",
    NotificationEvent.RECIPIENT_DELIVERY_FAILED_ATTEMPT: "Delivery Failed (Per Attempt)",
    NotificationEvent.RECIPIENT_DELIVERY_FAILED_FINAL: "Delivery Failed (Final — All Attempts Exhausted)",
    NotificationEvent.RECIPIENT_DELIVERED: "Delivered",
    NotificationEvent.RECIPIENT_CANCELLED: "Cancelled",
    NotificationEvent.DRIVER_VEHICLE_SERVICE_DUE: "Vehicle Service Due",
    NotificationEvent.DRIVER_WORK_SCHEDULE_UPDATED: "Work Schedule Updated",
}


class DevicePlatform(enum.StrEnum):
    IOS = "IOS"
    ANDROID = "ANDROID"
    WEB = "WEB"


CATEGORY_DISPLAY_NAMES: dict[NotificationCategory, str] = {
    NotificationCategory.SHIPMENT: "Shipment",
    NotificationCategory.BILLING: "Billing",
    NotificationCategory.ADMIN_ORDERS: "Orders",
    NotificationCategory.ADMIN_DRIVER_ISSUES: "Driver Issues",
    NotificationCategory.ADMIN_VEHICLE_ISSUES: "Vehicle Issues",
    NotificationCategory.ADMIN_ACCOUNT: "Account",
    NotificationCategory.ADMIN_CREDIT: "Credit",
    NotificationCategory.ADMIN_QUICKBOOKS: "QuickBooks",
    NotificationCategory.ADMIN_SYSTEM: "System",
    NotificationCategory.RECIPIENT_DELIVERY: "Delivery",
}


def category_display_name(category: NotificationCategory | None) -> str:
    """Human-readable label for a category (``'Other'`` for unknown)."""
    if category is None:
        return "Other"
    return CATEGORY_DISPLAY_NAMES.get(category, category.value.replace("_", " ").title())
