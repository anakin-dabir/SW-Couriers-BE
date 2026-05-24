from __future__ import annotations

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry

_CONFIG_ITEM_EXAMPLE = {
    "alert_type": "CREDIT_UTILISATION_MONITORING_WARNING",
    "enabled": True,
    "threshold_pct": "75.00",
    "score_drop_points": None,
    "reminder_days": None,
    "late_payment_count": None,
    "cooldown_period": "ONE_HOUR",
    "delivery_channel": "BOTH",
    "auto_acknowledge": True,
}

_CONFIG_LIST_EXAMPLE = {"items": [_CONFIG_ITEM_EXAMPLE]}

_SUMMARY_EXAMPLE = {
    "active_alerts_count": 5,
    "unacknowledged_alerts_count": 2,
    "last_alert_triggered_at": "2026-04-16T10:45:00Z",
}

_ALERT_ITEM_EXAMPLE = {
    "id": "alert-uuid",
    "organization_id": "org-uuid",
    "alert_type": "CREDIT_UTILISATION_MONITORING_CRITICAL",
    "severity": "CRITICAL",
    "status": "ACTIVE",
    "title": "Utilisation Critical",
    "summary": "Utilisation reached 92.3%, exceeding 90% threshold.",
    "context": {"utilisation_percent": 92.3, "org_name": "Acme Ltd"},
    "triggered_at": "2026-04-16T10:45:00Z",
    "snoozed_until": None,
    "acknowledged_at": None,
    "acknowledged_by": None,
    "resolution_notes": None,
    "resolved_at": None,
}

_ALERT_HISTORY_EXAMPLE = {
    "items": [
        {
            **_ALERT_ITEM_EXAMPLE,
            "status": "ACKNOWLEDGED",
            "acknowledged_at": "2026-04-16T11:00:00Z",
            "acknowledged_by": {"id": "user-uuid", "first_name": "John", "last_name": "Smith"},
            "resolution_notes": "Contacted customer about elevated usage.",
        },
    ],
    "total": 1,
    "page": 1,
    "size": 20,
    "pages": 1,
}


_GLOBAL_THRESHOLD_ITEM_EXAMPLE = {"alert_type": "CREDIT_UTILISATION_MONITORING_WARNING", "threshold_pct": "75.00"}
_GLOBAL_THRESHOLD_LIST_EXAMPLE = {
    "items": [
        {"alert_type": "CREDIT_UTILISATION_MONITORING_WARNING", "threshold_pct": "75.00"},
        {"alert_type": "CREDIT_UTILISATION_MONITORING_CRITICAL", "threshold_pct": "90.00"},
    ]
}

GET_GLOBAL_CREDIT_ALERT_THRESHOLDS = create_doc_entry(
    summary="Get global credit alert thresholds",
    description=(
        "`GET .../credit/alerts/global-thresholds`. Returns the system-wide default threshold percentages "
        "used as a fallback when an organisation has not configured its own thresholds."
    ),
    responses={
        200: success_entry("Global thresholds", data=_GLOBAL_THRESHOLD_LIST_EXAMPLE),
        401: error_401_entry(),
    },
)

PATCH_GLOBAL_CREDIT_ALERT_THRESHOLDS = create_doc_entry(
    summary="Update global credit alert thresholds",
    description=(
        "`PATCH .../credit/alerts/global-thresholds`. Updates one or more system-wide threshold percentages. "
        "Only the types included in the payload are modified."
    ),
    responses={
        200: success_entry("Global thresholds updated", data=_GLOBAL_THRESHOLD_LIST_EXAMPLE, message="Global thresholds updated."),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only super admins can update global thresholds."),
        422: error_entry("Validation failed", code="validation_error", message="Invalid threshold value."),
    },
)


GET_CREDIT_ALERT_SUMMARY = create_doc_entry(
    summary="Get credit alerts summary cards",
    description=(
        "`GET .../credit/alerts/summary`. Returns the three header cards shown on the Alerts tab: "
        "active alerts count, unacknowledged alerts count, and the timestamp of the last alert triggered."
    ),
    responses={
        200: success_entry("Alerts summary", data=_SUMMARY_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

GET_CREDIT_ALERTS_ACTIVE = create_doc_entry(
    summary="List active credit alerts",
    description=(
        "`GET .../credit/alerts/active`. Returns all alerts currently in `ACTIVE` or `SNOOZED` state for this "
        "organisation, newest first. Use the acknowledge and snooze endpoints to handle each one."
    ),
    responses={
        200: success_entry("Active alerts", data=[_ALERT_ITEM_EXAMPLE]),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

GET_CREDIT_ALERTS_HISTORY = create_doc_entry(
    summary="List credit alert history",
    description=(
        "`GET .../credit/alerts/history`. Paginated history of handled alerts. Optional filters: `statuses` "
        "(ACKNOWLEDGED, AUTO_ACKNOWLEDGED, RESOLVED) and `alert_types`. Supports standard `page` and `size` parameters."
    ),
    responses={
        200: success_entry("Alerts history", data=_ALERT_HISTORY_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

GET_CREDIT_ALERT_DETAIL = create_doc_entry(
    summary="Get credit alert detail",
    description=(
        "`GET .../credit/alerts/{alert_id}`. Returns the detail modal payload including status, timestamps, "
        "acknowledged-by user, and structured context data."
    ),
    responses={
        200: success_entry("Alert detail", data=_ALERT_ITEM_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Alert not found", code="not_found", message="Credit alert not found."),
    },
)

POST_CREDIT_ALERT_ACKNOWLEDGE = create_doc_entry(
    summary="Acknowledge a credit alert",
    description=(
        "`POST .../credit/alerts/{alert_id}/acknowledge`. Marks the alert as `ACKNOWLEDGED` by the caller. Accepts "
        "optional resolution notes (max 500 characters) describing the action taken."
    ),
    responses={
        200: success_entry("Alert acknowledged", data=_ALERT_ITEM_EXAMPLE, message="Alert acknowledged."),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can acknowledge alerts."),
        404: error_entry("Alert not found", code="not_found", message="Credit alert not found."),
        409: error_entry("Already handled", code="conflict", message="Alert has already been handled."),
    },
)

POST_CREDIT_ALERT_SNOOZE = create_doc_entry(
    summary="Snooze a credit alert",
    description=(
        "`POST .../credit/alerts/{alert_id}/snooze`. Snoozes the alert for the requested duration: `ONE_HOUR`, "
        "`FOUR_HOURS`, `TWENTY_FOUR_HOURS`, or `SEVEN_DAYS`. Snoozed alerts return to `ACTIVE` automatically when "
        "the snooze window elapses."
    ),
    responses={
        200: success_entry("Alert snoozed", data=_ALERT_ITEM_EXAMPLE, message="Alert snoozed."),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can snooze alerts."),
        404: error_entry("Alert not found", code="not_found", message="Credit alert not found."),
        409: error_entry("Cannot snooze", code="conflict", message="Only active alerts can be snoozed."),
    },
)

GET_CREDIT_ALERT_CONFIG = create_doc_entry(
    summary="Get credit alert configuration",
    description=(
        "`GET .../credit/alerts/config`. Returns one config item per alert type. If an organisation has never "
        "customised a given type, the system defaults are returned (see Figma for the default thresholds)."
    ),
    responses={
        200: success_entry("Alert configuration", data=_CONFIG_LIST_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)


PATCH_CREDIT_ALERT_CONFIG = create_doc_entry(
    summary="Update credit alert configuration",
    description=(
        "`PATCH .../credit/alerts/config`. Bulk upsert of alert configs. Pass each alert type you want to update "
        "with its alert-specific threshold fields, cooldown period, delivery channel, and auto-acknowledge toggle. Types not included "
        "in the payload are left unchanged."
    ),
    responses={
        200: success_entry("Alert configuration updated", data=_CONFIG_LIST_EXAMPLE, message="Alert configuration updated."),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can update alert configuration."),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
        422: error_entry("Validation failed", code="validation_error", message="Invalid alert configuration."),
    },
)
