"""OpenAPI documentation entries for notification endpoints.

One constant per endpoint. Each description makes three things explicit:

1. **Who** the route is for (the UI surface and the caller's role).
2. **Which notification types** are valid on that prefix.
3. **What happens** (cascade layer written, read, or wiped).

All success responses follow the envelope ``{ success, message?, data? }``.
"""

from __future__ import annotations

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry

_EVENT_EXAMPLE = {
    "event": "BOOKING_CONFIRMATION",
    "event_display_name": "Booking Created",
    "email": {"enabled": True, "default": True},
    "sms": {"enabled": False, "default": False},
    "template_customized": False,
}

_CATEGORY_EXAMPLE_SHIPMENT = {
    "category": "SHIPMENT",
    "category_display_name": "Shipment",
    "preferences": [_EVENT_EXAMPLE],
}

_CATEGORY_EXAMPLE_BILLING = {
    "category": "BILLING",
    "category_display_name": "Billing",
    "preferences": [
        {
            "event": "INVOICE_GENERATED",
            "event_display_name": "Invoice Generated",
            "email": {"enabled": True, "default": True},
            "sms": {"enabled": False, "default": False},
            "template_customized": False,
        }
    ],
}

_PREFS_EXAMPLE_ADMIN = [_CATEGORY_EXAMPLE_SHIPMENT, _CATEGORY_EXAMPLE_BILLING]
_PREFS_EXAMPLE_ORG = [_CATEGORY_EXAMPLE_SHIPMENT, _CATEGORY_EXAMPLE_BILLING]
_PREFS_EXAMPLE_B2B = [_CATEGORY_EXAMPLE_SHIPMENT, _CATEGORY_EXAMPLE_BILLING]

_TEMPLATE_EXAMPLE = {
    "subject": "Booking Confirmed — {{ tracking_number }}",
    "body": "Your booking has been confirmed.",
    "variables": ["tracking_number", "customer_name"],
    "source": "system",
    "is_custom": False,
}

_FORBIDDEN_ADMIN = error_entry(
    "Caller is not an admin",
    code="FORBIDDEN",
    message="This action requires one of: ADMIN",
)

_FORBIDDEN_ORG = error_entry(
    "Caller is neither an admin nor a contact of the given organization",
    code="FORBIDDEN",
    message="This action requires one of: ADMIN, CUSTOMER_B2B",
)

_FORBIDDEN_B2B = error_entry(
    "Caller is not a B2B customer contact",
    code="FORBIDDEN",
    message="This action requires one of: CUSTOMER_B2B",
)

_VALIDATION_ADMIN = error_entry(
    "Unsupported notification_type for /admin",
    code="VALIDATION_ERROR",
    message="notification_type must be ADMIN_INTERNAL, B2B_CUSTOMER, or RECIPIENT",
)

_VALIDATION_ORG = error_entry(
    "Unsupported notification_type for /organization",
    code="VALIDATION_ERROR",
    message="notification_type must be B2B_CUSTOMER or RECIPIENT",
)

_VALIDATION_B2B = error_entry(
    "Unsupported notification_type for /b2b_dashboard",
    code="VALIDATION_ERROR",
    message="notification_type must be B2B_CUSTOMER",
)

_VALIDATION_TEMPLATE = error_entry(
    "Invalid event or channel",
    code="VALIDATION_ERROR",
    message="Templates are editable only for EMAIL and SMS",
)


# Preferences — /admin


GET_ADMIN_PREFERENCES = create_doc_entry(
    "[Admin] Get resolved preferences for an admin notification type.",
    {
        200: success_entry("Resolved preferences grouped by category", data=_PREFS_EXAMPLE_ADMIN),
        401: error_401_entry(),
        403: _FORBIDDEN_ADMIN,
        422: _VALIDATION_ADMIN,
    },
    description=(
        "**Used by:** admin dashboard > Notifications tab.\n\n"
        "**Who can call:** ADMIN only.\n\n"
        "**Allowed notification_types:**\n"
        "- ``ADMIN_INTERNAL`` — returns the admin's own user preferences "
        "(cascade: user → hardcoded).\n"
        "- ``B2B_CUSTOMER`` / ``RECIPIENT`` — returns the global system "
        "defaults (cascade: system → hardcoded).\n\n"
        "Response is a list of category groups. Each group carries a "
        "``category`` enum value, a human-readable ``category_display_name``, "
        "and an inner ``preferences`` list. Each event in ``preferences`` has "
        "``email``/``sms`` objects with the effective ``enabled`` plus the "
        "hardcoded ``default``, and a single ``template_customized`` flag set "
        "to true whenever a custom email or sms template is pinned anywhere "
        "in the cascade."
    ),
)

UPDATE_ADMIN_PREFERENCES = create_doc_entry(
    "[Admin] Patch admin preferences or global defaults.",
    {
        200: success_entry("Preferences updated", message="Preferences updated"),
        401: error_401_entry(),
        403: _FORBIDDEN_ADMIN,
        422: _VALIDATION_ADMIN,
    },
    description=(
        "**Used by:** admin dashboard > Notifications tab > toggles.\n\n"
        "**Who can call:** ADMIN only.\n\n"
        "**Writes to:** the admin's own user prefs for ``ADMIN_INTERNAL``, "
        "the global system defaults for ``B2B_CUSTOMER`` / ``RECIPIENT``.\n\n"
        "Each event entry carries optional ``email`` and ``sms`` objects. "
        "Set ``enabled: null`` to clear the override (inherit from the next "
        "layer); omit the channel to leave it unchanged; pass ``true`` / "
        "``false`` to pin the value at this layer."
    ),
)

RESET_ADMIN_PREFERENCES = create_doc_entry(
    "[Admin] Reset every override at the admin layer for this notification type.",
    {
        200: success_entry("Preferences reset", message="Preferences reset"),
        401: error_401_entry(),
        403: _FORBIDDEN_ADMIN,
        422: _VALIDATION_ADMIN,
    },
    description=(
        "**Used by:** admin dashboard > Notifications tab > Reset button.\n\n"
        "**Who can call:** ADMIN only.\n\n"
        "**Effect:** wipes **both** toggle overrides **and** custom templates "
        "pinned at this layer for the given notification type. No separate "
        "template-reset call is needed. After this, the next layer of the "
        "cascade takes over:\n"
        "- ``ADMIN_INTERNAL`` → hardcoded defaults.\n"
        "- ``B2B_CUSTOMER`` / ``RECIPIENT`` → hardcoded defaults (system rows "
        "are deleted).\n\n"
        "Templates no longer referenced anywhere are hard-deleted."
    ),
)


# Preferences — /organization/{organization_id}


GET_ORG_PREFERENCES = create_doc_entry(
    "[Org] Get resolved preferences for an organization notification type.",
    {
        200: success_entry("Resolved preferences", data=_PREFS_EXAMPLE_ORG),
        401: error_401_entry(),
        403: _FORBIDDEN_ORG,
        422: _VALIDATION_ORG,
    },
    description=(
        "**Used by:** admin dashboard > B2B client settings, and by the "
        "B2B dashboard Recipient tab (same org the caller belongs to).\n\n"
        "**Who can call:** ADMIN, or CUSTOMER_B2B contacts of the given "
        "``organization_id``.\n\n"
        "**Allowed notification_types:** ``B2B_CUSTOMER``, ``RECIPIENT``.\n\n"
        "Response is grouped by category — see ``GET /preferences/admin/...`` "
        "for the shape. Values resolve through the full cascade (user → "
        "**org** → system → hardcoded) but this endpoint focuses on the org "
        "layer: each event carries the effective state plus the hardcoded "
        "``default`` the UI can revert to."
    ),
)

UPDATE_ORG_PREFERENCES = create_doc_entry(
    "[Org] Patch organization preference overrides.",
    {
        200: success_entry(
            "Organization preferences updated",
            message="Organization preferences updated",
        ),
        401: error_401_entry(),
        403: _FORBIDDEN_ORG,
        422: _VALIDATION_ORG,
    },
    description=(
        "**Used by:** admin dashboard > B2B client settings, and the B2B "
        "dashboard Recipient tab.\n\n"
        "**Who can call:** ADMIN, or CUSTOMER_B2B contacts of the given "
        "``organization_id``.\n\n"
        "**Writes to:** the ``org`` layer for the given organization.\n\n"
        "**Allowed notification_types:** ``B2B_CUSTOMER``, ``RECIPIENT``.\n\n"
        "``enabled: null`` clears the override (inherit from the system layer)."
    ),
)

RESET_ORG_PREFERENCES = create_doc_entry(
    "[Org] Reset every override at the org layer for this notification type.",
    {
        200: success_entry(
            "Organization preferences reset",
            message="Organization preferences reset",
        ),
        401: error_401_entry(),
        403: _FORBIDDEN_ORG,
        422: _VALIDATION_ORG,
    },
    description=(
        "**Used by:** admin dashboard > B2B client settings > Reset.\n\n"
        "**Who can call:** ADMIN, or CUSTOMER_B2B contacts of the given "
        "``organization_id``.\n\n"
        "**Effect:** deletes every org preference row for this "
        "``notification_type``, wiping **both** toggle overrides and any "
        "custom templates pinned at the org layer. The system layer (and "
        "hardcoded defaults below it) takes over. Templates no longer "
        "referenced anywhere are hard-deleted.\n\n"
        "**Allowed notification_types:** ``B2B_CUSTOMER``, ``RECIPIENT``."
    ),
)


# Preferences — /b2b_dashboard


GET_B2B_PREFERENCES = create_doc_entry(
    "[B2B] Get the caller's own B2B customer preferences.",
    {
        200: success_entry("Resolved preferences grouped by category", data=_PREFS_EXAMPLE_B2B),
        401: error_401_entry(),
        403: _FORBIDDEN_B2B,
        422: _VALIDATION_B2B,
    },
    description=(
        "**Used by:** B2B dashboard > My notifications tab.\n\n"
        "**Who can call:** CUSTOMER_B2B only.\n\n"
        "**Allowed notification_types:** ``B2B_CUSTOMER`` (the caller's own "
        "user preferences).\n\n"
        "Cascade for resolution: user → org (caller's org) → system → hardcoded. "
        "Response is grouped by category."
    ),
)

UPDATE_B2B_PREFERENCES = create_doc_entry(
    "[B2B] Patch the caller's own B2B customer preferences.",
    {
        200: success_entry("Preferences updated", message="Preferences updated"),
        401: error_401_entry(),
        403: _FORBIDDEN_B2B,
        422: _VALIDATION_B2B,
    },
    description=(
        "**Used by:** B2B dashboard > My notifications tab > toggles.\n\n"
        "**Who can call:** CUSTOMER_B2B only.\n\n"
        "**Writes to:** the caller's own user preferences.\n\n"
        "**Allowed notification_types:** ``B2B_CUSTOMER``."
    ),
)

RESET_B2B_PREFERENCES = create_doc_entry(
    "[B2B] Reset every override at the caller's own layer.",
    {
        200: success_entry("Preferences reset", message="Preferences reset"),
        401: error_401_entry(),
        403: _FORBIDDEN_B2B,
        422: _VALIDATION_B2B,
    },
    description=(
        "**Used by:** B2B dashboard > My notifications tab > Reset.\n\n"
        "**Who can call:** CUSTOMER_B2B only.\n\n"
        "**Effect:** deletes every user-level preference row for the caller "
        "+ ``B2B_CUSTOMER``, wiping **both** toggle overrides and any custom "
        "templates pinned at the user layer. The caller's organization "
        "defaults take over. Templates no longer referenced anywhere are "
        "hard-deleted.\n\n"
        "**Allowed notification_types:** ``B2B_CUSTOMER``."
    ),
)


# Templates — /admin


GET_ADMIN_TEMPLATE = create_doc_entry(
    "[Admin] Resolve a template for an admin notification type + event + channel.",
    {
        200: success_entry("Resolved template", data=_TEMPLATE_EXAMPLE),
        401: error_401_entry(),
        403: _FORBIDDEN_ADMIN,
        422: _VALIDATION_TEMPLATE,
    },
    description=(
        "**Used by:** admin dashboard > Notifications > template editor.\n\n"
        "**Who can call:** ADMIN only.\n\n"
        "**Allowed notification_types:** ``ADMIN_INTERNAL``, ``B2B_CUSTOMER``, "
        "``RECIPIENT``.\n\n"
        "**Allowed channels:** ``EMAIL``, ``SMS``.\n\n"
        "Walks the cascade user → org → system → hardcoded and returns the "
        "first non-null body. ``source`` identifies the originating layer so "
        "the UI can render 'inherited from system' etc. ``variables`` is "
        "always sourced from the hardcoded registry — pinning a custom "
        "template does not change the variable set."
    ),
)

UPSERT_ADMIN_TEMPLATE = create_doc_entry(
    "[Admin] Pin a custom template at the admin layer.",
    {
        200: success_entry(
            "Template pinned",
            data={**_TEMPLATE_EXAMPLE, "subject": "Custom", "source": "system", "is_custom": True},
        ),
        401: error_401_entry(),
        403: _FORBIDDEN_ADMIN,
        422: _VALIDATION_TEMPLATE,
    },
    description=(
        "**Used by:** admin dashboard > Notifications > Save template.\n\n"
        "**Who can call:** ADMIN only.\n\n"
        "**Writes to:** the admin's own user prefs for ``ADMIN_INTERNAL``; "
        "the system defaults row for ``B2B_CUSTOMER`` / ``RECIPIENT``.\n\n"
        "**Allowed channels:** ``EMAIL``, ``SMS``.\n\n"
        "Updates in place if a template is already pinned at this layer; "
        "otherwise creates a new template row and links it via the per-scope "
        "preference row. To clear **only** this channel's pinned template (leave "
        "toggles as-is), use ``POST /templates/admin/.../reset``. "
        "``POST /preferences/admin/.../reset`` still wipes **all** toggles and "
        "templates at the admin layer."
    ),
)

RESET_ADMIN_TEMPLATE = create_doc_entry(
    "[Admin] Clear the pinned template for one event + channel at the admin layer.",
    {
        200: success_entry(
            "Template reset — resolved view after clearing the pin",
            data={**_TEMPLATE_EXAMPLE, "is_custom": False},
        ),
        401: error_401_entry(),
        403: _FORBIDDEN_ADMIN,
        422: _VALIDATION_TEMPLATE,
    },
    description=(
        "**Used by:** admin dashboard > Notifications > Reset template (one channel).\n\n"
        "**Who can call:** ADMIN only.\n\n"
        "**Effect:** removes the ``email_template_id`` or ``sms_template_id`` pin at "
        "this layer for the given event; the live content then inherits from the "
        "next cascade layer. If the detached template row is unreferenced elsewhere, "
        "it is hard-deleted. Preference toggles at this layer are **not** changed.\n\n"
        "**Allowed notification_types:** ``ADMIN_INTERNAL``, ``B2B_CUSTOMER``, "
        "``RECIPIENT``.\n\n"
        "**Allowed channels:** ``EMAIL``, ``SMS``.\n\n"
        "If nothing was pinned at this layer, the response is the same as GET — idempotent."
    ),
)


# Templates — /organization/{organization_id}


GET_ORG_TEMPLATE = create_doc_entry(
    "[Org] Resolve a template for an organization notification type + event + channel.",
    {
        200: success_entry(
            "Resolved template",
            data={**_TEMPLATE_EXAMPLE, "source": "org"},
        ),
        401: error_401_entry(),
        403: _FORBIDDEN_ORG,
        422: _VALIDATION_TEMPLATE,
    },
    description=(
        "**Used by:** admin dashboard > B2B client > template editor, and "
        "B2B dashboard Recipient tab template editor.\n\n"
        "**Who can call:** ADMIN, or CUSTOMER_B2B contacts of the given "
        "``organization_id``.\n\n"
        "**Allowed notification_types:** ``B2B_CUSTOMER``, ``RECIPIENT``.\n\n"
        "**Allowed channels:** ``EMAIL``, ``SMS``.\n\n"
        "Resolution walks the cascade down from the org layer. ``variables`` "
        "always come from the hardcoded registry."
    ),
)

UPSERT_ORG_TEMPLATE = create_doc_entry(
    "[Org] Pin a custom template at the org layer.",
    {
        200: success_entry(
            "Template pinned",
            data={**_TEMPLATE_EXAMPLE, "source": "org", "is_custom": True},
        ),
        401: error_401_entry(),
        403: _FORBIDDEN_ORG,
        422: _VALIDATION_TEMPLATE,
    },
    description=(
        "**Used by:** admin dashboard > B2B client > Save template, and "
        "B2B dashboard Recipient template save.\n\n"
        "**Who can call:** ADMIN, or CUSTOMER_B2B contacts of the given "
        "``organization_id``.\n\n"
        "**Writes to:** the ``org`` layer for the given organization.\n\n"
        "**Allowed notification_types:** ``B2B_CUSTOMER``, ``RECIPIENT``.\n\n"
        "**Allowed channels:** ``EMAIL``, ``SMS``.\n\n"
        "To clear **only** this channel's template pin, use "
        "``POST /templates/organization/.../reset``. "
        "``POST /preferences/organization/.../reset`` clears **all** event rows "
        "for that notification type (toggles + templates)."
    ),
)

RESET_ORG_TEMPLATE = create_doc_entry(
    "[Org] Clear the pinned template for one event + channel at the org layer.",
    {
        200: success_entry(
            "Template reset — resolved view after clearing the pin",
            data={**_TEMPLATE_EXAMPLE, "source": "org", "is_custom": False},
        ),
        401: error_401_entry(),
        403: _FORBIDDEN_ORG,
        422: _VALIDATION_TEMPLATE,
    },
    description=(
        "**Used by:** admin B2B client settings or B2B dashboard > Reset one template.\n\n"
        "**Who can call:** ADMIN, or CUSTOMER_B2B contacts of the given ``organization_id``.\n\n"
        "**Effect:** nulls the template id column for this event and channel at the org "
        "layer only; inherited content comes from system / hardcoded. Unreferenced "
        "template rows are removed. Toggle overrides are unchanged.\n\n"
        "**Allowed notification_types:** ``B2B_CUSTOMER``, ``RECIPIENT``.\n\n"
        "**Allowed channels:** ``EMAIL``, ``SMS``.\n\n"
        "Idempotent when no org-level pin exists for that channel."
    ),
)


# Templates — /b2b_dashboard


GET_B2B_TEMPLATE = create_doc_entry(
    "[B2B] Resolve a template for the caller's own B2B customer stream.",
    {
        200: success_entry(
            "Resolved template",
            data={**_TEMPLATE_EXAMPLE, "source": "user"},
        ),
        401: error_401_entry(),
        403: _FORBIDDEN_B2B,
        422: _VALIDATION_TEMPLATE,
    },
    description=(
        "**Used by:** B2B dashboard > My notifications > template editor.\n\n"
        "**Who can call:** CUSTOMER_B2B only.\n\n"
        "**Allowed notification_types:** ``B2B_CUSTOMER``.\n\n"
        "**Allowed channels:** ``EMAIL``, ``SMS``."
    ),
)

UPSERT_B2B_TEMPLATE = create_doc_entry(
    "[B2B] Pin a custom template at the caller's own layer.",
    {
        200: success_entry(
            "Template pinned",
            data={**_TEMPLATE_EXAMPLE, "source": "user", "is_custom": True},
        ),
        401: error_401_entry(),
        403: _FORBIDDEN_B2B,
        422: _VALIDATION_TEMPLATE,
    },
    description=(
        "**Used by:** B2B dashboard > My notifications > Save template.\n\n"
        "**Who can call:** CUSTOMER_B2B only.\n\n"
        "**Writes to:** the caller's own user preferences.\n\n"
        "**Allowed notification_types:** ``B2B_CUSTOMER``.\n\n"
        "**Allowed channels:** ``EMAIL``, ``SMS``.\n\n"
        "To clear **only** this channel's template pin, use "
        "``POST /templates/b2b_dashboard/.../reset``. "
        "``POST /preferences/b2b_dashboard/.../reset`` clears **all** B2B_CUSTOMER "
        "overrides for the caller (toggles + templates)."
    ),
)

RESET_B2B_TEMPLATE = create_doc_entry(
    "[B2B] Clear the pinned template for one event + channel at the contact's layer.",
    {
        200: success_entry(
            "Template reset — resolved view after clearing the pin",
            data={**_TEMPLATE_EXAMPLE, "source": "user", "is_custom": False},
        ),
        401: error_401_entry(),
        403: _FORBIDDEN_B2B,
        422: _VALIDATION_TEMPLATE,
    },
    description=(
        "**Used by:** B2B dashboard > My notifications > Reset one template.\n\n"
        "**Who can call:** CUSTOMER_B2B only.\n\n"
        "**Effect:** removes the user-layer template pin for this event and channel; "
        "content inherits from org then system. Orphan template rows are deleted. "
        "Toggles unchanged.\n\n"
        "**Allowed notification_types:** ``B2B_CUSTOMER``.\n\n"
        "**Allowed channels:** ``EMAIL``, ``SMS``.\n\n"
        "Idempotent when no user-level pin exists."
    ),
)


# Devices, inbox, test — unchanged semantics


REGISTER_DEVICE = create_doc_entry(
    "Register a device token for push notifications.",
    {
        201: success_entry(
            "Device registered",
            data={
                "id": "...",
                "user_id": "...",
                "platform": "IOS",
                "is_active": True,
                "last_used_at": "2026-04-22T10:00:00Z",
            },
        ),
        401: error_401_entry(),
    },
    description=(
        "**Used by:** any signed-in mobile client.\n\n"
        "**Who can call:** any authenticated user (admin, B2B customer, driver, recipient)."
    ),
)

UNREGISTER_DEVICE = create_doc_entry(
    "Unregister a device token.",
    {
        200: success_entry("Device unregistered", message="Device unregistered"),
        401: error_401_entry(),
        404: error_entry(
            "Device token not found",
            code="NOT_FOUND",
            message="user_device_tokens with id '...' not found",
        ),
    },
    description="Any authenticated user can unregister one of their own tokens.",
)

LIST_EVENTS_FOR_TYPE = create_doc_entry(
    "List notification events for a notification type.",
    {
        200: success_entry(
            "Events supported for this notification type",
            data=[
                {"event": "BOOKING_CONFIRMATION", "event_display_name": "Booking Created"},
                {"event": "PICKUP_SCHEDULED", "event_display_name": "Pickup Scheduled"},
            ],
        ),
        401: error_401_entry(),
        422: error_entry(
            "Unknown notification_type",
            code="VALIDATION_ERROR",
            message="Invalid notification_type 'XYZ'. Must be one of: ADMIN_INTERNAL, B2B_CUSTOMER, RECIPIENT",
        ),
    },
    description=(
        "**Used by:** every notification preferences screen (admin, organization settings, "
        "b2b dashboard) to render the event list before filling in overrides.\n\n"
        "**Who can call:** ADMIN or CUSTOMER_B2B.\n\n"
        "**Allowed notification_types:** ``ADMIN_INTERNAL``, ``B2B_CUSTOMER``, ``RECIPIENT``.\n\n"
        "Returns every event that can belong to the given notification type, together "
        "with its human-readable display name. The list is ordered the way the UI should "
        "render it and is the same set of events returned by the preferences GET."
    ),
)

SEND_TEST = create_doc_entry(
    "Send a test notification for a configured event.",
    {
        200: success_entry(
            "Per-channel send results in request order",
            data={
                "results": [
                    {"channel": "EMAIL", "status": "SENT", "error": None},
                    {"channel": "SMS", "status": "FAILED", "error": "Twilio not configured"},
                ]
            },
        ),
        401: error_401_entry(),
        403: error_entry(
            "Caller is neither an admin nor a B2B customer contact",
            code="FORBIDDEN",
            message="This action requires one of: ADMIN, CUSTOMER_B2B",
        ),
        422: error_entry(
            "Missing contact field, bad scope/type/event combination, or unsupported channel",
            code="VALIDATION_ERROR",
            message="email is required when channels includes EMAIL",
        ),
    },
    description=(
        "**Used by:** the 'Send test' button on every notification template editor.\n\n"
        "**Who can call:** ADMIN or CUSTOMER_B2B — each resolving from the cascade layer "
        "that matches the ``scope`` they send.\n\n"
        "**Request body:** ``scope`` (``ADMIN`` | ``ORGANIZATION`` | ``B2B_DASHBOARD``), "
        "``notification_type`` (``ADMIN_INTERNAL`` | ``B2B_CUSTOMER`` | ``RECIPIENT``), "
        "``event``, ``channels`` (subset of ``['EMAIL', 'SMS']``, at least one), "
        "``email`` (required if ``EMAIL`` in channels), ``phone_number`` (required if "
        "``SMS`` in channels), ``organization_id`` (required if ``scope`` is ``ORGANIZATION``).\n\n"
        "**Effect:** for each requested channel the backend walks the full template "
        "cascade (user → org → system → hardcoded) for the ``(scope, notification_type, event)`` "
        "tuple, renders subject/body with placeholder values from the hardcoded variable registry, "
        "and dispatches via the real EMAIL / SMS providers. Returns one result entry per channel."
    ),
)

LIST_MY_NOTIFICATIONS = create_doc_entry(
    "List my notifications (inbox).",
    {
        200: success_entry(
            "Paginated inbox notifications",
            data={
                "items": [
                    {
                        "id": "...",
                        "event": "BOOKING_CONFIRMATION",
                        "notification_type": "B2B_CUSTOMER",
                        "subject": "Booking Confirmed",
                        "body": "Booking #123 has been confirmed.",
                        "context_json": {
                            "route_id": "705c87f2-e376-4c9e-8349-8d1c6abbcd88",
                            "stop_id": "e5cadb7d-5252-48ab-9788-fbfef9202146",
                        },
                        "read_at": None,
                        "created_at": "2026-03-16T10:00:00Z",
                    },
                ],
                "total": 42,
                "page": 1,
                "size": 20,
                "pages": 3,
            },
        ),
        401: error_401_entry(),
    },
    description=(
        "**Used by:** every dashboard bell icon.\n\n"
        "**Who can call:** any authenticated user.\n\n"
        "Query params: ``page``, ``size``, ``unread_only``. Each item may include "
        "``context_json`` when the notification was created with routing metadata (e.g. "
        "``route_id``, ``stop_id``)."
    ),
)

GET_UNREAD_COUNT = create_doc_entry(
    "Get unread notification count.",
    {
        200: success_entry("Unread count", data={"unread_count": 5}),
        401: error_401_entry(),
    },
    description="Any authenticated user, returns their own unread count.",
)

MARK_READ = create_doc_entry(
    "Mark a notification as read.",
    {
        200: success_entry("Marked as read", message="Notification marked as read"),
        401: error_401_entry(),
        404: error_entry(
            "Notification not found or already read",
            code="NOT_FOUND",
            message="Notification not found or already read",
        ),
    },
    description="Any authenticated user, acts on their own inbox only.",
)

MARK_ALL_READ = create_doc_entry(
    "Mark all notifications as read.",
    {
        200: success_entry("All marked as read", message="3 notifications marked as read"),
        401: error_401_entry(),
    },
    description="Any authenticated user, marks their entire inbox as read.",
)
