"""Hardcoded default notification templates — not seeded to DB.

Each template is keyed as ``{EVENT}_{STREAM}_{CHANNEL}`` where ``STREAM`` is
the ``NotificationType`` value (``ADMIN_INTERNAL`` / ``B2B_CUSTOMER`` /
``RECIPIENT`` / ``DRIVER``).

Variables are kept **separate** from the subject/body strings. When the API
returns a template (hardcoded *or* a custom one pinned by a user / org /
system), the ``variables`` list always comes from this file — callers never
mutate the variable registry from a request. If the variable set for an
event ever changes, update it here in code.

Resolution order lives in ``service.py``. Reset at a layer removes the
override and lets the next layer down take over; ``hardcoded`` is the final
fallback.
"""

from __future__ import annotations

# Variable sets per event (channel-agnostic — shared by email and sms for the
# same event/stream). Keeping these in a dedicated registry means the subject /
# body strings stay focused on content and the variable list stays a single
# source of truth.

_VARS_BOOKING = ["customer_first_name", "customer_full_name", "tracking_number", "pickup_date", "customer_address", "short_tracking_link"]
_VARS_PICKUP_SCHEDULED = ["customer_first_name", "customer_full_name", "tracking_number", "pickup_date", "pickup_time_window", "pickup_address", "short_tracking_link"]
_VARS_PICKUP_ON_THE_WAY = ["customer_first_name", "customer_full_name", "tracking_number", "pickup_time_window", "pickup_address", "driver_name", "short_tracking_link"]
_VARS_PICKUP_COMPLETED = ["customer_first_name", "customer_full_name", "tracking_number", "pickup_address", "driver_name", "short_tracking_link"]
_VARS_IN_TRANSIT = ["customer_first_name", "customer_full_name", "tracking_number", "short_tracking_link"]
_VARS_WAREHOUSE = ["customer_first_name", "customer_full_name", "tracking_number", "warehouse_name", "short_tracking_link"]
_VARS_OUT_FOR_DELIVERY = ["customer_first_name", "customer_full_name", "tracking_number", "customer_address", "package_weight", "driver_name", "short_tracking_link"]
_VARS_DELIVERED = ["customer_first_name", "customer_full_name", "tracking_number", "customer_address", "delivered_at", "short_tracking_link"]
_VARS_DELIVERY_PARTIAL = ["customer_first_name", "customer_full_name", "tracking_number", "delivered_count", "total_count", "short_tracking_link"]
_VARS_DELIVERY_FAILED_ATTEMPT = ["customer_first_name", "customer_full_name", "tracking_number", "failure_reason", "attempt_number", "max_attempts", "next_attempt_date", "short_tracking_link"]
_VARS_DELIVERY_FAILED_FINAL = ["customer_first_name", "customer_full_name", "tracking_number", "failure_reason", "attempt_number", "short_tracking_link"]
_VARS_RETURN_INITIATED = ["customer_first_name", "customer_full_name", "tracking_number", "return_reason", "short_tracking_link"]
_VARS_RETURN_SCHEDULED = ["customer_first_name", "customer_full_name", "tracking_number", "pickup_date", "pickup_time_window", "pickup_address", "short_tracking_link"]
_VARS_RETURN_IN_TRANSIT = ["customer_first_name", "customer_full_name", "tracking_number", "short_tracking_link"]
_VARS_RETURN_COMPLETED = ["customer_first_name", "customer_full_name", "tracking_number", "completed_at", "short_tracking_link"]
_VARS_RETURNED_TO_SENDER = ["customer_first_name", "customer_full_name", "tracking_number", "sender_address", "short_tracking_link"]
_VARS_BOOKING_DISPOSED = ["customer_first_name", "customer_full_name", "tracking_number", "dispose_reason", "disposed_at"]

_VARS_INVOICE = ["customer_first_name", "customer_full_name", "invoice_number", "invoice_amount", "due_date", "invoice_link"]
_VARS_INVOICE_OVERDUE = ["customer_first_name", "customer_full_name", "invoice_number", "invoice_amount", "due_date", "days_overdue", "invoice_link"]
_VARS_PAYMENT_RECEIVED = ["customer_first_name", "customer_full_name", "invoice_number", "payment_amount", "payment_date", "payment_method"]
_VARS_CREDIT_WARNING = ["customer_first_name", "customer_full_name", "credit_limit", "credit_balance", "credit_usage_percent"]
_VARS_CREDIT_REACHED = ["customer_first_name", "customer_full_name", "credit_limit", "credit_balance"]

_VARS_ADMIN_ORDER = ["order_id", "tracking_number", "customer_full_name", "account_name", "pickup_address", "delivery_address", "package_count", "order_link"]
_VARS_ADMIN_ORDER_FAILED = ["order_id", "tracking_number", "customer_full_name", "account_name", "failure_reason", "attempt_number", "order_link"]
_VARS_ADMIN_ORDER_CANCELLED = ["order_id", "tracking_number", "customer_full_name", "account_name", "cancellation_reason", "cancelled_by", "order_link"]
_VARS_ADMIN_PACKAGE_MISSING = ["order_id", "tracking_number", "customer_full_name", "account_name", "driver_name", "reported_at", "order_link"]
_VARS_ADMIN_PACKAGE_DAMAGED = ["order_id", "tracking_number", "customer_full_name", "account_name", "driver_name", "damage_description", "reported_at", "order_link"]
_VARS_ADMIN_REPORTED_DEFECTS = ["vehicle_registration", "driver_name", "defect_description", "severity", "reported_at", "defect_link"]
_VARS_ADMIN_VEHICLE_BREAKDOWN = ["vehicle_registration", "driver_name", "location", "breakdown_reason", "reported_at", "vehicle_link"]
_VARS_ADMIN_VEHICLE_MAINTENANCE = ["vehicle_registration", "service_type", "due_date", "last_service_date", "vehicle_link"]
_VARS_ADMIN_DRIVER_ACCOUNT = ["driver_name", "driver_id", "reason", "effective_date", "driver_link"]
_VARS_ADMIN_CLIENT_ACCOUNT = ["account_name", "account_id", "reason", "effective_date", "account_link"]
_VARS_ADMIN_QB_CONNECTION = ["connection_status", "realm_id", "connected_at", "last_refreshed_at", "last_error_at", "last_error", "admin_qb_settings_url", "raw_error_detail"]
_VARS_ADMIN_DATA_SYNC = ["sync_job_type", "entity_type", "failed_count", "error_code", "error_message", "failed_since", "admin_link"]
_VARS_ADMIN_DELAYED_ORDERS = ["delayed_orders_count", "window_hours", "threshold", "dashboard_link"]
_VARS_RECIPIENT_BASE = ["customer_first_name", "tracking_number", "short_tracking_link"]
_VARS_RECIPIENT_SCHEDULED = ["customer_first_name", "tracking_number", "pickup_date", "pickup_time_window", "short_tracking_link"]
_VARS_RECIPIENT_PARTIAL = ["customer_first_name", "tracking_number", "delivered_count", "total_count", "short_tracking_link"]
_VARS_RECIPIENT_FAILED = ["customer_first_name", "tracking_number", "failure_reason", "attempt_number", "short_tracking_link"]
_VARS_RECIPIENT_CANCELLED = ["customer_first_name", "tracking_number", "cancellation_reason"]

_VARS_DRIVER_JOB = ["tracking_number", "pickup_address", "customer_full_name", "pickup_time_window"]
_VARS_DRIVER_DELIVER = ["tracking_number", "customer_full_name", "customer_address"]
_VARS_DRIVER_TRACKING = ["tracking_number"]
_VARS_DRIVER_FAILED = ["tracking_number", "failure_reason"]


def _tpl(name: str, channel: str, subject: str | None, body: str, variables: list[str]) -> dict:
    return {"name": name, "channel": channel, "subject": subject, "body": body, "variables": variables}


_TEMPLATES: list[dict] = [
    # ╔══════════════════════════════════════════════════════════════════════╗
    # ║ B2B_CUSTOMER — shipment lifecycle (seen by the B2B contact / org)    ║
    # ╚══════════════════════════════════════════════════════════════════════╝
    _tpl(
        "BOOKING_CONFIRMATION_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Booking confirmed — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your booking has been confirmed.\n\n"
            "Tracking number: {{ tracking_number }}\n"
            "Pickup date: {{ pickup_date }}\n"
            "Delivery address: {{ customer_address }}\n\n"
            "Track here: {{ short_tracking_link }}\n\n"
            "Thank you for choosing SW Couriers."
        ),
        _VARS_BOOKING,
    ),
    _tpl(
        "BOOKING_CONFIRMATION_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: booking {{ tracking_number }} confirmed. Pickup {{ pickup_date }}. Track: {{ short_tracking_link }}",
        _VARS_BOOKING,
    ),
    _tpl(
        "PICKUP_SCHEDULED_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Pickup scheduled — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Pickup has been scheduled for your shipment.\n\n"
            "Tracking number: {{ tracking_number }}\n"
            "Pickup date: {{ pickup_date }}\n"
            "Time window: {{ pickup_time_window }}\n"
            "Pickup address: {{ pickup_address }}\n\n"
            "Track here: {{ short_tracking_link }}"
        ),
        _VARS_PICKUP_SCHEDULED,
    ),
    _tpl(
        "PICKUP_SCHEDULED_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: pickup for {{ tracking_number }} scheduled {{ pickup_date }} ({{ pickup_time_window }}).",
        _VARS_PICKUP_SCHEDULED,
    ),
    _tpl(
        "PICKUP_ON_THE_WAY_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Your driver is on the way — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "{{ driver_name }} is on the way to collect your shipment.\n\n"
            "Tracking number: {{ tracking_number }}\n"
            "Estimated arrival: {{ pickup_time_window }}\n"
            "Pickup address: {{ pickup_address }}\n\n"
            "Track here: {{ short_tracking_link }}"
        ),
        _VARS_PICKUP_ON_THE_WAY,
    ),
    _tpl(
        "PICKUP_ON_THE_WAY_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: {{ driver_name }} is on the way for {{ tracking_number }}. ETA {{ pickup_time_window }}.",
        _VARS_PICKUP_ON_THE_WAY,
    ),
    _tpl(
        "PICKUP_COMPLETED_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Package picked up — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your package has been picked up and is on its way.\n\n"
            "Tracking number: {{ tracking_number }}\n"
            "Picked up from: {{ pickup_address }}\n"
            "Driver: {{ driver_name }}\n\n"
            "Track here: {{ short_tracking_link }}"
        ),
        _VARS_PICKUP_COMPLETED,
    ),
    _tpl(
        "PICKUP_COMPLETED_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: {{ tracking_number }} picked up. Track: {{ short_tracking_link }}",
        _VARS_PICKUP_COMPLETED,
    ),
    _tpl(
        "IN_TRANSIT_TO_WAREHOUSE_B2B_CUSTOMER_EMAIL", "EMAIL",
        "In transit — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your shipment is in transit to our warehouse.\n\n"
            "Tracking number: {{ tracking_number }}\n\n"
            "Track here: {{ short_tracking_link }}"
        ),
        _VARS_IN_TRANSIT,
    ),
    _tpl(
        "IN_TRANSIT_TO_WAREHOUSE_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: {{ tracking_number }} in transit to warehouse. Track: {{ short_tracking_link }}",
        _VARS_IN_TRANSIT,
    ),
    _tpl(
        "PACKAGE_IN_WAREHOUSE_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Arrived at warehouse — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your shipment has arrived at {{ warehouse_name }} and is being processed.\n\n"
            "Tracking number: {{ tracking_number }}\n\n"
            "Track here: {{ short_tracking_link }}"
        ),
        _VARS_WAREHOUSE,
    ),
    _tpl(
        "PACKAGE_IN_WAREHOUSE_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: {{ tracking_number }} arrived at {{ warehouse_name }}. Track: {{ short_tracking_link }}",
        _VARS_WAREHOUSE,
    ),
    _tpl(
        "OUT_FOR_DELIVERY_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Out for delivery — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your shipment is out for delivery today.\n\n"
            "Tracking number: {{ tracking_number }}\n"
            "Delivery address: {{ customer_address }}\n"
            "Package weight: {{ package_weight }}\n"
            "Driver: {{ driver_name }}\n\n"
            "Track here: {{ short_tracking_link }}"
        ),
        _VARS_OUT_FOR_DELIVERY,
    ),
    _tpl(
        "OUT_FOR_DELIVERY_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: {{ tracking_number }} is out for delivery today. Track: {{ short_tracking_link }}",
        _VARS_OUT_FOR_DELIVERY,
    ),
    _tpl(
        "DELIVERY_SUCCESSFUL_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Delivered — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your shipment has been delivered successfully.\n\n"
            "Tracking number: {{ tracking_number }}\n"
            "Delivered at: {{ delivered_at }}\n"
            "Delivered to: {{ customer_address }}\n\n"
            "Thank you for choosing SW Couriers."
        ),
        _VARS_DELIVERED,
    ),
    _tpl(
        "DELIVERY_SUCCESSFUL_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: {{ tracking_number }} delivered at {{ delivered_at }}. Thank you!",
        _VARS_DELIVERED,
    ),
    _tpl(
        "DELIVERY_PARTIAL_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Partial delivery — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Part of your shipment has been delivered. Remaining items will follow.\n\n"
            "Tracking number: {{ tracking_number }}\n"
            "Delivered: {{ delivered_count }} of {{ total_count }}\n\n"
            "Track here: {{ short_tracking_link }}"
        ),
        _VARS_DELIVERY_PARTIAL,
    ),
    _tpl(
        "DELIVERY_PARTIAL_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: partial delivery for {{ tracking_number }} ({{ delivered_count }}/{{ total_count }}).",
        _VARS_DELIVERY_PARTIAL,
    ),
    _tpl(
        "DELIVERY_FAILED_ATTEMPT_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Delivery attempt failed — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "We attempted delivery for your shipment but were unable to complete it.\n\n"
            "Tracking number: {{ tracking_number }}\n"
            "Attempt: {{ attempt_number }} of {{ max_attempts }}\n"
            "Reason: {{ failure_reason }}\n"
            "Next attempt: {{ next_attempt_date }}\n\n"
            "Track here: {{ short_tracking_link }}"
        ),
        _VARS_DELIVERY_FAILED_ATTEMPT,
    ),
    _tpl(
        "DELIVERY_FAILED_ATTEMPT_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: delivery attempt {{ attempt_number }}/{{ max_attempts }} failed for {{ tracking_number }} ({{ failure_reason }}).",
        _VARS_DELIVERY_FAILED_ATTEMPT,
    ),
    _tpl(
        "DELIVERY_FAILED_FINAL_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Delivery failed — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "After {{ attempt_number }} attempts we were unable to deliver your shipment.\n\n"
            "Tracking number: {{ tracking_number }}\n"
            "Reason: {{ failure_reason }}\n\n"
            "Please contact us to arrange re-delivery or collection.\n"
            "Track here: {{ short_tracking_link }}"
        ),
        _VARS_DELIVERY_FAILED_FINAL,
    ),
    _tpl(
        "DELIVERY_FAILED_FINAL_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: final delivery attempt failed for {{ tracking_number }}. Contact us to arrange re-delivery.",
        _VARS_DELIVERY_FAILED_FINAL,
    ),
    _tpl(
        "RETURN_INITIATED_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Return initiated — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "A return has been initiated for your shipment.\n\n"
            "Tracking number: {{ tracking_number }}\n"
            "Reason: {{ return_reason }}\n\n"
            "Track here: {{ short_tracking_link }}"
        ),
        _VARS_RETURN_INITIATED,
    ),
    _tpl(
        "RETURN_INITIATED_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: return initiated for {{ tracking_number }} ({{ return_reason }}).",
        _VARS_RETURN_INITIATED,
    ),
    _tpl(
        "RETURN_SCHEDULED_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Return collection scheduled — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Return collection has been scheduled.\n\n"
            "Tracking number: {{ tracking_number }}\n"
            "Collection date: {{ pickup_date }}\n"
            "Time window: {{ pickup_time_window }}\n"
            "Pickup address: {{ pickup_address }}\n\n"
            "Track here: {{ short_tracking_link }}"
        ),
        _VARS_RETURN_SCHEDULED,
    ),
    _tpl(
        "RETURN_SCHEDULED_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: return collection for {{ tracking_number }} scheduled {{ pickup_date }} ({{ pickup_time_window }}).",
        _VARS_RETURN_SCHEDULED,
    ),
    _tpl(
        "RETURN_IN_TRANSIT_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Return in transit — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your return is in transit.\n\n"
            "Tracking number: {{ tracking_number }}\n\n"
            "Track here: {{ short_tracking_link }}"
        ),
        _VARS_RETURN_IN_TRANSIT,
    ),
    _tpl(
        "RETURN_IN_TRANSIT_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: return {{ tracking_number }} in transit. Track: {{ short_tracking_link }}",
        _VARS_RETURN_IN_TRANSIT,
    ),
    _tpl(
        "RETURN_COMPLETED_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Return completed — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your return has been completed.\n\n"
            "Tracking number: {{ tracking_number }}\n"
            "Completed at: {{ completed_at }}\n\n"
            "Thank you for choosing SW Couriers."
        ),
        _VARS_RETURN_COMPLETED,
    ),
    _tpl(
        "RETURN_COMPLETED_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: return {{ tracking_number }} completed at {{ completed_at }}.",
        _VARS_RETURN_COMPLETED,
    ),
    _tpl(
        "RETURNED_TO_SENDER_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Returned to sender — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your shipment has been returned to the sender.\n\n"
            "Tracking number: {{ tracking_number }}\n"
            "Sender address: {{ sender_address }}\n\n"
            "Track here: {{ short_tracking_link }}"
        ),
        _VARS_RETURNED_TO_SENDER,
    ),
    _tpl(
        "RETURNED_TO_SENDER_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: {{ tracking_number }} returned to sender.",
        _VARS_RETURNED_TO_SENDER,
    ),
    _tpl(
        "BOOKING_DISPOSED_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Booking disposed — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your booking could not be delivered or returned and has been disposed.\n\n"
            "Tracking number: {{ tracking_number }}\n"
            "Reason: {{ dispose_reason }}\n"
            "Disposed at: {{ disposed_at }}\n\n"
            "Please contact us if you have any questions."
        ),
        _VARS_BOOKING_DISPOSED,
    ),
    _tpl(
        "BOOKING_DISPOSED_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: booking {{ tracking_number }} disposed ({{ dispose_reason }}).",
        _VARS_BOOKING_DISPOSED,
    ),

    # B2B_CUSTOMER — billing (shared event names, customer-facing wording)
    _tpl(
        "INVOICE_GENERATED_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Invoice {{ invoice_number }} — {{ invoice_amount }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "A new invoice has been generated for your account.\n\n"
            "Invoice number: {{ invoice_number }}\n"
            "Amount: {{ invoice_amount }}\n"
            "Due date: {{ due_date }}\n\n"
            "View invoice: {{ invoice_link }}"
        ),
        _VARS_INVOICE,
    ),
    _tpl(
        "INVOICE_GENERATED_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: invoice {{ invoice_number }} ({{ invoice_amount }}) is due {{ due_date }}.",
        _VARS_INVOICE,
    ),
    _tpl(
        "INVOICE_OVERDUE_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Invoice overdue — {{ invoice_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your invoice is now {{ days_overdue }} day(s) overdue.\n\n"
            "Invoice number: {{ invoice_number }}\n"
            "Amount: {{ invoice_amount }}\n"
            "Due date: {{ due_date }}\n\n"
            "Please arrange payment to avoid service disruption.\n"
            "View invoice: {{ invoice_link }}"
        ),
        _VARS_INVOICE_OVERDUE,
    ),
    _tpl(
        "INVOICE_OVERDUE_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: invoice {{ invoice_number }} ({{ invoice_amount }}) is {{ days_overdue }}d overdue. Please pay asap.",
        _VARS_INVOICE_OVERDUE,
    ),
    _tpl(
        "PAYMENT_RECEIVED_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Payment received — {{ invoice_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "We have received your payment.\n\n"
            "Invoice number: {{ invoice_number }}\n"
            "Amount paid: {{ payment_amount }}\n"
            "Paid on: {{ payment_date }}\n"
            "Method: {{ payment_method }}\n\n"
            "Thank you."
        ),
        _VARS_PAYMENT_RECEIVED,
    ),
    _tpl(
        "PAYMENT_RECEIVED_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: payment of {{ payment_amount }} received for {{ invoice_number }}. Thank you!",
        _VARS_PAYMENT_RECEIVED,
    ),
    _tpl(
        "CREDIT_UTILISATION_MONITORING_WARNING_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Credit limit warning",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your account is at {{ credit_usage_percent }}% of its credit limit.\n\n"
            "Credit limit: {{ credit_limit }}\n"
            "Current balance: {{ credit_balance }}\n\n"
            "Please make a payment to avoid service interruption."
        ),
        _VARS_CREDIT_WARNING,
    ),
    _tpl(
        "CREDIT_UTILISATION_MONITORING_WARNING_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: credit usage at {{ credit_usage_percent }}%. Please make a payment.",
        _VARS_CREDIT_WARNING,
    ),
    _tpl(
        "CREDIT_UTILISATION_MONITORING_CRITICAL_B2B_CUSTOMER_EMAIL", "EMAIL",
        "Credit limit reached",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your account has reached 100% of its credit limit. New bookings may be restricted.\n\n"
            "Credit limit: {{ credit_limit }}\n"
            "Current balance: {{ credit_balance }}\n\n"
            "Please make a payment immediately to restore service."
        ),
        _VARS_CREDIT_REACHED,
    ),
    _tpl(
        "CREDIT_UTILISATION_MONITORING_CRITICAL_B2B_CUSTOMER_SMS", "SMS", None,
        "SW Couriers: credit limit reached. Please make a payment to restore service.",
        _VARS_CREDIT_REACHED,
    ),

    # ╔══════════════════════════════════════════════════════════════════════╗
    # ║ ADMIN_INTERNAL — platform operations                                 ║
    # ╚══════════════════════════════════════════════════════════════════════╝
    _tpl(
        "ADMIN_NEW_ORDER_CREATED_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "New order — {{ tracking_number }}",
        (
            "A new order has been created.\n\n"
            "Order: {{ order_id }} ({{ tracking_number }})\n"
            "Customer: {{ customer_full_name }}\n"
            "Account: {{ account_name }}\n"
            "Pickup: {{ pickup_address }}\n"
            "Delivery: {{ delivery_address }}\n"
            "Packages: {{ package_count }}\n\n"
            "Open in admin: {{ order_link }}"
        ),
        _VARS_ADMIN_ORDER,
    ),
    _tpl(
        "ADMIN_NEW_ORDER_CREATED_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: new order {{ tracking_number }} — {{ account_name }}.",
        _VARS_ADMIN_ORDER,
    ),
    _tpl(
        "ADMIN_ORDER_DELIVERED_SUCCESSFULLY_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "Order delivered — {{ tracking_number }}",
        (
            "Order delivered successfully.\n\n"
            "Order: {{ order_id }} ({{ tracking_number }})\n"
            "Customer: {{ customer_full_name }}\n"
            "Account: {{ account_name }}\n\n"
            "Open in admin: {{ order_link }}"
        ),
        _VARS_ADMIN_ORDER,
    ),
    _tpl(
        "ADMIN_ORDER_DELIVERED_SUCCESSFULLY_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: {{ tracking_number }} delivered ({{ account_name }}).",
        _VARS_ADMIN_ORDER,
    ),
    _tpl(
        "ADMIN_ORDER_DELIVERY_FAILED_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "Delivery failed — {{ tracking_number }}",
        (
            "An order delivery has failed.\n\n"
            "Order: {{ order_id }} ({{ tracking_number }})\n"
            "Customer: {{ customer_full_name }}\n"
            "Account: {{ account_name }}\n"
            "Attempt: {{ attempt_number }}\n"
            "Reason: {{ failure_reason }}\n\n"
            "Open in admin: {{ order_link }}"
        ),
        _VARS_ADMIN_ORDER_FAILED,
    ),
    _tpl(
        "ADMIN_ORDER_DELIVERY_FAILED_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: delivery failed {{ tracking_number }} ({{ failure_reason }}).",
        _VARS_ADMIN_ORDER_FAILED,
    ),
    _tpl(
        "ADMIN_ORDER_CANCELLED_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "Order cancelled — {{ tracking_number }}",
        (
            "An order has been cancelled.\n\n"
            "Order: {{ order_id }} ({{ tracking_number }})\n"
            "Customer: {{ customer_full_name }}\n"
            "Account: {{ account_name }}\n"
            "Reason: {{ cancellation_reason }}\n"
            "Cancelled by: {{ cancelled_by }}\n\n"
            "Open in admin: {{ order_link }}"
        ),
        _VARS_ADMIN_ORDER_CANCELLED,
    ),
    _tpl(
        "ADMIN_ORDER_CANCELLED_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: order {{ tracking_number }} cancelled ({{ cancellation_reason }}).",
        _VARS_ADMIN_ORDER_CANCELLED,
    ),
    _tpl(
        "ADMIN_PACKAGE_MISSING_REPORTED_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "Package reported missing — {{ tracking_number }}",
        (
            "A driver has reported a missing package.\n\n"
            "Order: {{ order_id }} ({{ tracking_number }})\n"
            "Customer: {{ customer_full_name }}\n"
            "Account: {{ account_name }}\n"
            "Driver: {{ driver_name }}\n"
            "Reported at: {{ reported_at }}\n\n"
            "Open in admin: {{ order_link }}"
        ),
        _VARS_ADMIN_PACKAGE_MISSING,
    ),
    _tpl(
        "ADMIN_PACKAGE_MISSING_REPORTED_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: missing package reported {{ tracking_number }} by {{ driver_name }}.",
        _VARS_ADMIN_PACKAGE_MISSING,
    ),
    _tpl(
        "ADMIN_PACKAGE_DAMAGED_REPORTED_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "Package reported damaged — {{ tracking_number }}",
        (
            "A driver has reported a damaged package.\n\n"
            "Order: {{ order_id }} ({{ tracking_number }})\n"
            "Customer: {{ customer_full_name }}\n"
            "Account: {{ account_name }}\n"
            "Driver: {{ driver_name }}\n"
            "Damage: {{ damage_description }}\n"
            "Reported at: {{ reported_at }}\n\n"
            "Open in admin: {{ order_link }}"
        ),
        _VARS_ADMIN_PACKAGE_DAMAGED,
    ),
    _tpl(
        "ADMIN_PACKAGE_DAMAGED_REPORTED_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: damaged package {{ tracking_number }} — {{ damage_description }}.",
        _VARS_ADMIN_PACKAGE_DAMAGED,
    ),
    _tpl(
        "ADMIN_REPORTED_DEFECTS_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "Defect reported — {{ vehicle_registration }}",
        (
            "A defect has been reported on a vehicle.\n\n"
            "Vehicle: {{ vehicle_registration }}\n"
            "Driver: {{ driver_name }}\n"
            "Severity: {{ severity }}\n"
            "Defect: {{ defect_description }}\n"
            "Reported at: {{ reported_at }}\n\n"
            "Open in admin: {{ defect_link }}"
        ),
        _VARS_ADMIN_REPORTED_DEFECTS,
    ),
    _tpl(
        "ADMIN_REPORTED_DEFECTS_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: defect on {{ vehicle_registration }} ({{ severity }}) — {{ defect_description }}.",
        _VARS_ADMIN_REPORTED_DEFECTS,
    ),
    _tpl(
        "ADMIN_VEHICLE_BREAKDOWN_REPORTED_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "Vehicle breakdown — {{ vehicle_registration }}",
        (
            "A vehicle breakdown has been reported.\n\n"
            "Vehicle: {{ vehicle_registration }}\n"
            "Driver: {{ driver_name }}\n"
            "Location: {{ location }}\n"
            "Reason: {{ breakdown_reason }}\n"
            "Reported at: {{ reported_at }}\n\n"
            "Open in admin: {{ vehicle_link }}"
        ),
        _VARS_ADMIN_VEHICLE_BREAKDOWN,
    ),
    _tpl(
        "ADMIN_VEHICLE_BREAKDOWN_REPORTED_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: breakdown {{ vehicle_registration }} @ {{ location }} ({{ breakdown_reason }}).",
        _VARS_ADMIN_VEHICLE_BREAKDOWN,
    ),
    _tpl(
        "ADMIN_VEHICLE_MAINTENANCE_DUE_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "Vehicle maintenance due — {{ vehicle_registration }}",
        (
            "Maintenance is due for a vehicle.\n\n"
            "Vehicle: {{ vehicle_registration }}\n"
            "Service type: {{ service_type }}\n"
            "Due date: {{ due_date }}\n"
            "Last service: {{ last_service_date }}\n\n"
            "Open in admin: {{ vehicle_link }}"
        ),
        _VARS_ADMIN_VEHICLE_MAINTENANCE,
    ),
    _tpl(
        "ADMIN_VEHICLE_MAINTENANCE_DUE_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: {{ service_type }} due for {{ vehicle_registration }} by {{ due_date }}.",
        _VARS_ADMIN_VEHICLE_MAINTENANCE,
    ),
    _tpl(
        "ADMIN_DRIVER_ACCOUNT_SUSPENDED_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "Driver suspended — {{ driver_name }}",
        (
            "A driver account has been suspended.\n\n"
            "Driver: {{ driver_name }} ({{ driver_id }})\n"
            "Reason: {{ reason }}\n"
            "Effective: {{ effective_date }}\n\n"
            "Open in admin: {{ driver_link }}"
        ),
        _VARS_ADMIN_DRIVER_ACCOUNT,
    ),
    _tpl(
        "ADMIN_DRIVER_ACCOUNT_SUSPENDED_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: driver {{ driver_name }} suspended ({{ reason }}).",
        _VARS_ADMIN_DRIVER_ACCOUNT,
    ),
    _tpl(
        "ADMIN_DRIVER_ACCOUNT_DELETED_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "Driver deleted — {{ driver_name }}",
        (
            "A driver account has been deleted.\n\n"
            "Driver: {{ driver_name }} ({{ driver_id }})\n"
            "Reason: {{ reason }}\n"
            "Effective: {{ effective_date }}\n\n"
            "Open in admin: {{ driver_link }}"
        ),
        _VARS_ADMIN_DRIVER_ACCOUNT,
    ),
    _tpl(
        "ADMIN_DRIVER_ACCOUNT_DELETED_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: driver {{ driver_name }} deleted.",
        _VARS_ADMIN_DRIVER_ACCOUNT,
    ),
    _tpl(
        "ADMIN_CLIENT_ACCOUNT_SUSPENDED_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "Client account suspended — {{ account_name }}",
        (
            "A client (B2B) account has been suspended.\n\n"
            "Account: {{ account_name }} ({{ account_id }})\n"
            "Reason: {{ reason }}\n"
            "Effective: {{ effective_date }}\n\n"
            "Open in admin: {{ account_link }}"
        ),
        _VARS_ADMIN_CLIENT_ACCOUNT,
    ),
    _tpl(
        "ADMIN_CLIENT_ACCOUNT_SUSPENDED_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: {{ account_name }} suspended ({{ reason }}).",
        _VARS_ADMIN_CLIENT_ACCOUNT,
    ),
    _tpl(
        "ADMIN_CLIENT_ACCOUNT_DELETED_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "Client account deleted — {{ account_name }}",
        (
            "A client (B2B) account has been deleted.\n\n"
            "Account: {{ account_name }} ({{ account_id }})\n"
            "Reason: {{ reason }}\n"
            "Effective: {{ effective_date }}\n\n"
            "Open in admin: {{ account_link }}"
        ),
        _VARS_ADMIN_CLIENT_ACCOUNT,
    ),
    _tpl(
        "ADMIN_CLIENT_ACCOUNT_DELETED_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: client {{ account_name }} deleted.",
        _VARS_ADMIN_CLIENT_ACCOUNT,
    ),
    _tpl(
        "ADMIN_QUICKBOOKS_CONNECTION_FAILURE_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "QuickBooks connection failure",
        (
            "The QuickBooks connection is failing.\n\n"
            "Status: {{ connection_status }}\n"
            "Realm: {{ realm_id }}\n"
            "Connected at: {{ connected_at }}\n"
            "Last refreshed: {{ last_refreshed_at }}\n"
            "Last error at: {{ last_error_at }}\n"
            "Last error: {{ last_error }}\n\n"
            "Reconnect: {{ admin_qb_settings_url }}\n\n"
            "{% if raw_error_detail %}Technical detail:\n{{ raw_error_detail }}\n{% endif %}"
        ),
        _VARS_ADMIN_QB_CONNECTION,
    ),
    _tpl(
        "ADMIN_QUICKBOOKS_CONNECTION_FAILURE_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: QuickBooks {{ connection_status }} — reconnect required.",
        _VARS_ADMIN_QB_CONNECTION,
    ),
    _tpl(
        "ADMIN_DATA_SYNC_FAILURE_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "Data sync failure — {{ sync_job_type }}",
        (
            "A data sync job is failing.\n\n"
            "Job: {{ sync_job_type }}\n"
            "Entity: {{ entity_type }}\n"
            "Failed jobs: {{ failed_count }}\n"
            "Since: {{ failed_since }}\n"
            "Error code: {{ error_code }}\n"
            "Error: {{ error_message }}\n\n"
            "Open in admin: {{ admin_link }}"
        ),
        _VARS_ADMIN_DATA_SYNC,
    ),
    _tpl(
        "ADMIN_DATA_SYNC_FAILURE_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: {{ sync_job_type }} sync failing ({{ failed_count }}).",
        _VARS_ADMIN_DATA_SYNC,
    ),
    _tpl(
        "ADMIN_HIGH_NUMBER_OF_DELAYED_ORDERS_ADMIN_INTERNAL_EMAIL", "EMAIL",
        "High number of delayed orders",
        (
            "A high number of delayed orders has been detected.\n\n"
            "Delayed orders: {{ delayed_orders_count }} in the last {{ window_hours }}h (threshold: {{ threshold }})\n\n"
            "Open dashboard: {{ dashboard_link }}"
        ),
        _VARS_ADMIN_DELAYED_ORDERS,
    ),
    _tpl(
        "ADMIN_HIGH_NUMBER_OF_DELAYED_ORDERS_ADMIN_INTERNAL_SMS", "SMS", None,
        "Admin: {{ delayed_orders_count }} delayed orders in {{ window_hours }}h.",
        _VARS_ADMIN_DELAYED_ORDERS,
    ),

    # ╔══════════════════════════════════════════════════════════════════════╗
    # ║ RECIPIENT — end-customer delivery updates                            ║
    # ╚══════════════════════════════════════════════════════════════════════╝
    _tpl(
        "RECIPIENT_PENDING_PICKUP_RECIPIENT_EMAIL", "EMAIL",
        "Shipment on the way — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "A shipment is on its way to you.\n\n"
            "Tracking number: {{ tracking_number }}\n\n"
            "Track: {{ short_tracking_link }}"
        ),
        _VARS_RECIPIENT_BASE,
    ),
    _tpl(
        "RECIPIENT_PENDING_PICKUP_RECIPIENT_SMS", "SMS", None,
        "SW Couriers: shipment {{ tracking_number }} on the way. Track: {{ short_tracking_link }}",
        _VARS_RECIPIENT_BASE,
    ),
    _tpl(
        "RECIPIENT_PICKUP_SCHEDULED_RECIPIENT_EMAIL", "EMAIL",
        "Pickup scheduled — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Pickup for your shipment has been scheduled.\n\n"
            "Tracking: {{ tracking_number }}\n"
            "Pickup date: {{ pickup_date }}\n"
            "Time window: {{ pickup_time_window }}\n\n"
            "Track: {{ short_tracking_link }}"
        ),
        _VARS_RECIPIENT_SCHEDULED,
    ),
    _tpl(
        "RECIPIENT_PICKUP_SCHEDULED_RECIPIENT_SMS", "SMS", None,
        "SW Couriers: pickup for {{ tracking_number }} on {{ pickup_date }} ({{ pickup_time_window }}).",
        _VARS_RECIPIENT_SCHEDULED,
    ),
    _tpl(
        "RECIPIENT_AT_WAREHOUSE_RECIPIENT_EMAIL", "EMAIL",
        "Shipment at warehouse — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your shipment is at the SW Couriers warehouse and is being processed.\n\n"
            "Tracking: {{ tracking_number }}\n\n"
            "Track: {{ short_tracking_link }}"
        ),
        _VARS_RECIPIENT_BASE,
    ),
    _tpl(
        "RECIPIENT_AT_WAREHOUSE_RECIPIENT_SMS", "SMS", None,
        "SW Couriers: {{ tracking_number }} at warehouse. Track: {{ short_tracking_link }}",
        _VARS_RECIPIENT_BASE,
    ),
    _tpl(
        "RECIPIENT_DELIVERY_SCHEDULED_RECIPIENT_EMAIL", "EMAIL",
        "Delivery scheduled — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Delivery for your shipment has been scheduled.\n\n"
            "Tracking: {{ tracking_number }}\n"
            "Delivery date: {{ pickup_date }}\n"
            "Time window: {{ pickup_time_window }}\n\n"
            "Track: {{ short_tracking_link }}"
        ),
        _VARS_RECIPIENT_SCHEDULED,
    ),
    _tpl(
        "RECIPIENT_DELIVERY_SCHEDULED_RECIPIENT_SMS", "SMS", None,
        "SW Couriers: delivery for {{ tracking_number }} scheduled {{ pickup_date }} ({{ pickup_time_window }}).",
        _VARS_RECIPIENT_SCHEDULED,
    ),
    _tpl(
        "RECIPIENT_OUT_FOR_DELIVERY_RECIPIENT_EMAIL", "EMAIL",
        "Out for delivery — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your shipment is out for delivery today.\n\n"
            "Tracking: {{ tracking_number }}\n\n"
            "Track: {{ short_tracking_link }}"
        ),
        _VARS_RECIPIENT_BASE,
    ),
    _tpl(
        "RECIPIENT_OUT_FOR_DELIVERY_RECIPIENT_SMS", "SMS", None,
        "SW Couriers: {{ tracking_number }} is out for delivery today. Track: {{ short_tracking_link }}",
        _VARS_RECIPIENT_BASE,
    ),
    _tpl(
        "RECIPIENT_PARTIALLY_DELIVERED_RECIPIENT_EMAIL", "EMAIL",
        "Partially delivered — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Part of your shipment has been delivered. Remaining items will follow.\n\n"
            "Tracking: {{ tracking_number }}\n"
            "Delivered: {{ delivered_count }} of {{ total_count }}\n\n"
            "Track: {{ short_tracking_link }}"
        ),
        _VARS_RECIPIENT_PARTIAL,
    ),
    _tpl(
        "RECIPIENT_PARTIALLY_DELIVERED_RECIPIENT_SMS", "SMS", None,
        "SW Couriers: {{ tracking_number }} partially delivered ({{ delivered_count }}/{{ total_count }}).",
        _VARS_RECIPIENT_PARTIAL,
    ),
    _tpl(
        "RECIPIENT_DELIVERY_FAILED_ATTEMPT_RECIPIENT_EMAIL", "EMAIL",
        "Delivery attempt — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "We attempted delivery for your shipment but were unable to complete it.\n\n"
            "Tracking: {{ tracking_number }}\n"
            "Attempt: {{ attempt_number }}\n"
            "Reason: {{ failure_reason }}\n\n"
            "Track: {{ short_tracking_link }}"
        ),
        _VARS_RECIPIENT_FAILED,
    ),
    _tpl(
        "RECIPIENT_DELIVERY_FAILED_ATTEMPT_RECIPIENT_SMS", "SMS", None,
        "SW Couriers: delivery attempt {{ attempt_number }} failed for {{ tracking_number }} ({{ failure_reason }}).",
        _VARS_RECIPIENT_FAILED,
    ),
    _tpl(
        "RECIPIENT_DELIVERY_FAILED_FINAL_RECIPIENT_EMAIL", "EMAIL",
        "Delivery failed — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "We were unable to deliver your shipment after {{ attempt_number }} attempts.\n\n"
            "Tracking: {{ tracking_number }}\n"
            "Reason: {{ failure_reason }}\n\n"
            "Please contact SW Couriers to arrange collection.\n"
            "Track: {{ short_tracking_link }}"
        ),
        _VARS_RECIPIENT_FAILED,
    ),
    _tpl(
        "RECIPIENT_DELIVERY_FAILED_FINAL_RECIPIENT_SMS", "SMS", None,
        "SW Couriers: final delivery failure for {{ tracking_number }}. Please contact us.",
        _VARS_RECIPIENT_FAILED,
    ),
    _tpl(
        "RECIPIENT_DELIVERED_RECIPIENT_EMAIL", "EMAIL",
        "Delivered — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Your shipment has been delivered.\n\n"
            "Tracking: {{ tracking_number }}\n\n"
            "Thank you for choosing SW Couriers."
        ),
        _VARS_RECIPIENT_BASE,
    ),
    _tpl(
        "RECIPIENT_DELIVERED_RECIPIENT_SMS", "SMS", None,
        "SW Couriers: {{ tracking_number }} delivered. Thank you!",
        _VARS_RECIPIENT_BASE,
    ),
    _tpl(
        "RECIPIENT_CANCELLED_RECIPIENT_EMAIL", "EMAIL",
        "Shipment cancelled — {{ tracking_number }}",
        (
            "Hi {{ customer_first_name }},\n\n"
            "Shipment {{ tracking_number }} has been cancelled.\n\n"
            "Reason: {{ cancellation_reason }}"
        ),
        _VARS_RECIPIENT_CANCELLED,
    ),
    _tpl(
        "RECIPIENT_CANCELLED_RECIPIENT_SMS", "SMS", None,
        "SW Couriers: {{ tracking_number }} cancelled ({{ cancellation_reason }}).",
        _VARS_RECIPIENT_CANCELLED,
    ),

    # ╔══════════════════════════════════════════════════════════════════════╗
    # ║ DRIVER — legacy in-app / push templates                              ║
    # ╚══════════════════════════════════════════════════════════════════════╝
    _tpl(
        "BOOKING_CONFIRMATION_DRIVER_PUSH", "PUSH",
        "New job assigned",
        "Booking {{ tracking_number }} — pickup from {{ pickup_address }}.",
        _VARS_DRIVER_JOB,
    ),
    _tpl(
        "BOOKING_CONFIRMATION_DRIVER_IN_APP", "IN_APP",
        "New job assigned",
        "Booking {{ tracking_number }} assigned. Pickup: {{ pickup_address }}.",
        _VARS_DRIVER_JOB,
    ),
    _tpl(
        "PICKUP_SCHEDULED_DRIVER_PUSH", "PUSH",
        "Pickup assigned",
        "Pickup {{ tracking_number }} scheduled. Window {{ pickup_time_window }}.",
        _VARS_DRIVER_JOB,
    ),
    _tpl(
        "PICKUP_SCHEDULED_DRIVER_IN_APP", "IN_APP",
        "Pickup assigned",
        "Pickup {{ tracking_number }}. Address {{ pickup_address }}.",
        _VARS_DRIVER_JOB,
    ),
    _tpl(
        "PICKUP_ON_THE_WAY_DRIVER_PUSH", "PUSH",
        "Head to pickup",
        "En route to {{ pickup_address }} for {{ tracking_number }}.",
        _VARS_DRIVER_JOB,
    ),
    _tpl(
        "PICKUP_ON_THE_WAY_DRIVER_IN_APP", "IN_APP",
        "Head to pickup",
        "Collect {{ tracking_number }} from {{ pickup_address }}.",
        _VARS_DRIVER_JOB,
    ),
    _tpl(
        "PICKUP_COMPLETED_DRIVER_PUSH", "PUSH",
        "Pickup confirmed",
        "Pickup {{ tracking_number }} confirmed.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "PICKUP_COMPLETED_DRIVER_IN_APP", "IN_APP",
        "Pickup confirmed",
        "Pickup {{ tracking_number }} marked as completed.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "IN_TRANSIT_TO_WAREHOUSE_DRIVER_PUSH", "PUSH",
        "Deliver to warehouse",
        "Take {{ tracking_number }} to the warehouse.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "IN_TRANSIT_TO_WAREHOUSE_DRIVER_IN_APP", "IN_APP",
        "Deliver to warehouse",
        "Package {{ tracking_number }} — deliver to warehouse.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "PACKAGE_IN_WAREHOUSE_DRIVER_PUSH", "PUSH",
        "Warehouse check-in",
        "Package {{ tracking_number }} checked into warehouse.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "PACKAGE_IN_WAREHOUSE_DRIVER_IN_APP", "IN_APP",
        "Warehouse check-in",
        "Package {{ tracking_number }} checked into warehouse.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "OUT_FOR_DELIVERY_DRIVER_PUSH", "PUSH",
        "Delivery job",
        "Deliver {{ tracking_number }} to {{ customer_address }}.",
        _VARS_DRIVER_DELIVER,
    ),
    _tpl(
        "OUT_FOR_DELIVERY_DRIVER_IN_APP", "IN_APP",
        "Delivery assigned",
        "Deliver {{ tracking_number }} to {{ customer_address }}.",
        _VARS_DRIVER_DELIVER,
    ),
    _tpl(
        "DELIVERY_SUCCESSFUL_DRIVER_PUSH", "PUSH",
        "Delivery confirmed",
        "Delivery {{ tracking_number }} confirmed.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "DELIVERY_SUCCESSFUL_DRIVER_IN_APP", "IN_APP",
        "Delivery confirmed",
        "Delivery {{ tracking_number }} marked as completed.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "DELIVERY_PARTIAL_DRIVER_PUSH", "PUSH",
        "Partial delivery recorded",
        "{{ tracking_number }} partially delivered.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "DELIVERY_PARTIAL_DRIVER_IN_APP", "IN_APP",
        "Partial delivery recorded",
        "Partial delivery for {{ tracking_number }}.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "DELIVERY_FAILED_ATTEMPT_DRIVER_PUSH", "PUSH",
        "Delivery failed",
        "Delivery {{ tracking_number }} failed ({{ failure_reason }}).",
        _VARS_DRIVER_FAILED,
    ),
    _tpl(
        "DELIVERY_FAILED_ATTEMPT_DRIVER_IN_APP", "IN_APP",
        "Delivery failed — re-attempt",
        "Delivery {{ tracking_number }} failed: {{ failure_reason }}. Please re-attempt.",
        _VARS_DRIVER_FAILED,
    ),
    _tpl(
        "DELIVERY_FAILED_FINAL_DRIVER_PUSH", "PUSH",
        "Delivery cancelled",
        "Delivery {{ tracking_number }} cancelled.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "DELIVERY_FAILED_FINAL_DRIVER_IN_APP", "IN_APP",
        "Delivery cancelled",
        "Delivery {{ tracking_number }} cancelled after all attempts.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "RETURN_INITIATED_DRIVER_PUSH", "PUSH",
        "Return pickup",
        "Return pickup for {{ tracking_number }} from {{ customer_address }}.",
        _VARS_DRIVER_DELIVER,
    ),
    _tpl(
        "RETURN_INITIATED_DRIVER_IN_APP", "IN_APP",
        "Return pickup assigned",
        "Return pickup for {{ tracking_number }}. Address: {{ customer_address }}.",
        _VARS_DRIVER_DELIVER,
    ),
    _tpl(
        "RETURN_SCHEDULED_DRIVER_PUSH", "PUSH",
        "Return collection assigned",
        "Collect return {{ tracking_number }} from {{ pickup_address }}.",
        _VARS_DRIVER_JOB,
    ),
    _tpl(
        "RETURN_SCHEDULED_DRIVER_IN_APP", "IN_APP",
        "Return collection assigned",
        "Return collection for {{ tracking_number }}. Address: {{ pickup_address }}.",
        _VARS_DRIVER_JOB,
    ),
    _tpl(
        "RETURN_IN_TRANSIT_DRIVER_PUSH", "PUSH",
        "Return in transit",
        "Return {{ tracking_number }} in transit to depot.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "RETURN_IN_TRANSIT_DRIVER_IN_APP", "IN_APP",
        "Return in transit",
        "Return {{ tracking_number }} is in transit to depot.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "RETURN_COMPLETED_DRIVER_PUSH", "PUSH",
        "Return confirmed",
        "Return {{ tracking_number }} confirmed.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "RETURN_COMPLETED_DRIVER_IN_APP", "IN_APP",
        "Return confirmed",
        "Return {{ tracking_number }} marked as completed.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "RETURNED_TO_SENDER_DRIVER_PUSH", "PUSH",
        "Return delivered",
        "Return {{ tracking_number }} delivered to sender.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "RETURNED_TO_SENDER_DRIVER_IN_APP", "IN_APP",
        "Return delivered",
        "Return {{ tracking_number }} marked as returned to sender.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "BOOKING_DISPOSED_DRIVER_PUSH", "PUSH",
        "Booking disposed",
        "Booking {{ tracking_number }} marked as disposed.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "BOOKING_DISPOSED_DRIVER_IN_APP", "IN_APP",
        "Booking disposed",
        "Booking {{ tracking_number }} has been disposed.",
        _VARS_DRIVER_TRACKING,
    ),
    _tpl(
        "DRIVER_VEHICLE_SERVICE_DUE_DRIVER_PUSH", "PUSH",
        "Vehicle service due",
        "{{ due_detail }}",
        [],
    ),
    _tpl(
        "DRIVER_VEHICLE_SERVICE_DUE_DRIVER_IN_APP", "IN_APP",
        "Vehicle service due",
        "{{ due_detail }}",
        [],
    ),
    _tpl(
        "DRIVER_WORK_SCHEDULE_UPDATED_DRIVER_PUSH", "PUSH",
        "Work schedule updated",
        "{{ change_summary }}",
        ["driver_name", "change_summary", "effective_from", "updated_at"],
    ),
    _tpl(
        "DRIVER_WORK_SCHEDULE_UPDATED_DRIVER_IN_APP", "IN_APP",
        "Work schedule updated",
        "{{ change_summary }}",
        ["driver_name", "change_summary", "effective_from", "updated_at"],
    ),
]


_TEMPLATES_BY_NAME: dict[str, dict] = {tpl["name"]: tpl for tpl in _TEMPLATES}


def get_default_template(name: str) -> dict | None:
    """Look up a hardcoded template by its ``{EVENT}_{STREAM}_{CHANNEL}`` name.

    Returns a dict with ``name`` / ``subject`` / ``body`` / ``variables`` when
    found, otherwise ``None``. ``variables`` is always a fresh copy so callers
    may mutate it freely.
    """
    tpl = _TEMPLATES_BY_NAME.get(name)
    if tpl is None:
        return None
    return {
        "name": tpl["name"],
        "subject": tpl.get("subject") or "",
        "body": tpl["body"],
        "variables": list(tpl.get("variables", [])),
    }


def get_hardcoded_for_context(event: str, notification_type: str, channel: str) -> dict | None:
    """Look up a hardcoded template for an ``(event, stream, channel)`` context.

    Stream names map 1:1 — no bucket aliasing. The three user-facing streams
    (``ADMIN_INTERNAL``, ``B2B_CUSTOMER``, ``RECIPIENT``) each own their own
    copy of shared events so admin wording can differ from customer wording.
    """
    return get_default_template(f"{event}_{notification_type}_{channel}")


def get_hardcoded_variables(event: str, notification_type: str, channel: str) -> list[str]:
    """Variables registered for an ``(event, stream, channel)`` context.

    Always served from this file — custom DB templates do not override the
    variable registry. Empty list if no hardcoded template exists.
    """
    tpl = _TEMPLATES_BY_NAME.get(f"{event}_{notification_type}_{channel}")
    if tpl is None:
        return []
    return list(tpl.get("variables", []))
