"""Hardcoded code-level preference defaults (layer 1 — the absolute bottom).

Used as the final fallback when no row exists at system, org, or user layer
for a given channel. Returns a channel -> bool dict matching the wide DB
shape.
"""

from app.modules.notifications.enums import (
    NotificationChannel,
    NotificationEvent,
    NotificationType,
)

ChannelDefaults = dict[NotificationChannel, bool]

_ch = NotificationChannel


DEFAULT_ADMIN_INTERNAL_PREFERENCES: dict[NotificationEvent, ChannelDefaults] = {
    NotificationEvent.ADMIN_NEW_ORDER_CREATED: {_ch.EMAIL: False, _ch.SMS: False},
    NotificationEvent.ADMIN_ORDER_DELIVERED_SUCCESSFULLY: {_ch.EMAIL: False, _ch.SMS: False},
    NotificationEvent.ADMIN_ORDER_DELIVERY_FAILED: {_ch.EMAIL: False, _ch.SMS: False},
    NotificationEvent.ADMIN_ORDER_CANCELLED: {_ch.EMAIL: False, _ch.SMS: False},
    NotificationEvent.ADMIN_PACKAGE_MISSING_REPORTED: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.ADMIN_PACKAGE_DAMAGED_REPORTED: {_ch.EMAIL: True, _ch.SMS: False},
    NotificationEvent.ADMIN_REPORTED_DEFECTS: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.ADMIN_VEHICLE_BREAKDOWN_REPORTED: {_ch.EMAIL: True, _ch.SMS: False},
    NotificationEvent.ADMIN_VEHICLE_MAINTENANCE_DUE: {_ch.EMAIL: True, _ch.SMS: False},
    NotificationEvent.ADMIN_DRIVER_ACCOUNT_SUSPENDED: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.ADMIN_DRIVER_ACCOUNT_DELETED: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.ADMIN_CLIENT_ACCOUNT_SUSPENDED: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.ADMIN_CLIENT_ACCOUNT_DELETED: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.ADMIN_QUICKBOOKS_CONNECTION_FAILURE: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.ADMIN_DATA_SYNC_FAILURE: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.ADMIN_HIGH_NUMBER_OF_DELAYED_ORDERS: {_ch.EMAIL: True, _ch.SMS: True},
}

DEFAULT_B2B_CUSTOMER_PREFERENCES: dict[NotificationEvent, ChannelDefaults] = {
    NotificationEvent.BOOKING_CONFIRMATION: {_ch.EMAIL: True, _ch.SMS: False},
    NotificationEvent.PICKUP_SCHEDULED: {_ch.EMAIL: True, _ch.SMS: False},
    NotificationEvent.PICKUP_ON_THE_WAY: {_ch.EMAIL: True, _ch.SMS: False},
    NotificationEvent.PICKUP_COMPLETED: {_ch.EMAIL: False, _ch.SMS: False},
    NotificationEvent.IN_TRANSIT_TO_WAREHOUSE: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.PACKAGE_IN_WAREHOUSE: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.OUT_FOR_DELIVERY: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.DELIVERY_SUCCESSFUL: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.DELIVERY_PARTIAL: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.DELIVERY_FAILED_ATTEMPT: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.DELIVERY_FAILED_FINAL: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.RETURN_INITIATED: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.RETURN_SCHEDULED: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.RETURN_IN_TRANSIT: {_ch.EMAIL: True, _ch.SMS: False},
    NotificationEvent.RETURN_COMPLETED: {_ch.EMAIL: True, _ch.SMS: False},
    NotificationEvent.RETURNED_TO_SENDER: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.BOOKING_DISPOSED: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.INVOICE_GENERATED: {_ch.EMAIL: True, _ch.SMS: False},
    NotificationEvent.INVOICE_OVERDUE: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.PAYMENT_RECEIVED: {_ch.EMAIL: True, _ch.SMS: False},
    NotificationEvent.CREDIT_UTILISATION_MONITORING_WARNING: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.CREDIT_UTILISATION_MONITORING_CRITICAL: {_ch.EMAIL: True, _ch.SMS: True},
}

DEFAULT_RECIPIENT_PREFERENCES: dict[NotificationEvent, ChannelDefaults] = {
    NotificationEvent.RECIPIENT_PENDING_PICKUP: {_ch.EMAIL: True, _ch.SMS: False},
    NotificationEvent.RECIPIENT_PICKUP_SCHEDULED: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.RECIPIENT_AT_WAREHOUSE: {_ch.EMAIL: True, _ch.SMS: False},
    NotificationEvent.RECIPIENT_DELIVERY_SCHEDULED: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.RECIPIENT_OUT_FOR_DELIVERY: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.RECIPIENT_PARTIALLY_DELIVERED: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.RECIPIENT_DELIVERY_FAILED_ATTEMPT: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.RECIPIENT_DELIVERY_FAILED_FINAL: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.RECIPIENT_DELIVERED: {_ch.EMAIL: True, _ch.SMS: True},
    NotificationEvent.RECIPIENT_CANCELLED: {_ch.EMAIL: True, _ch.SMS: True},
}

_DEFAULTS_BY_TYPE: dict[NotificationType, dict[NotificationEvent, ChannelDefaults]] = {
    NotificationType.ADMIN_INTERNAL: DEFAULT_ADMIN_INTERNAL_PREFERENCES,
    NotificationType.B2B_CUSTOMER: DEFAULT_B2B_CUSTOMER_PREFERENCES,
    NotificationType.RECIPIENT: DEFAULT_RECIPIENT_PREFERENCES,
}


def get_event_defaults(
    event: NotificationEvent,
    notification_type: NotificationType = NotificationType.RECIPIENT,
) -> ChannelDefaults:
    """Return the hardcoded ``{channel: enabled}`` map for an event+stream."""
    type_map = _DEFAULTS_BY_TYPE.get(notification_type, {})
    return type_map.get(event, {})


def get_event_channel_default(
    event: NotificationEvent,
    notification_type: NotificationType,
    channel: NotificationChannel,
) -> bool:
    """Single-channel hardcoded default — False when nothing is configured."""
    return _DEFAULTS_BY_TYPE.get(notification_type, {}).get(event, {}).get(channel, False)
