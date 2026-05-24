"""OpenAPI documentation snippets for QuickBooks integration API."""

from __future__ import annotations

from typing import Any

from app.core.swagger.utils import request_body_openapi

_EX_ORG = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_EX_INVOICE = "95259d07-e4e9-42c6-80f3-708aea874475"
_EX_CREDIT_NOTE = "c4e5f6a7-b8c9-4012-d3e4-f5a6b7c8d9e0"
_EX_PAYMENT = "bf5eca49-ed0f-4e8b-95d8-7e51bd237af6"

_QB_SYNC_BODY = request_body_openapi(
    examples={
        "default_queue": {
            "summary": "Queue sync (default)",
            "value": {"force": False},
        },
        "force_full_resync": {
            "summary": "Force full upsert (invoice also re-applies credits)",
            "value": {"force": True},
        },
    }
)

QB_CONNECT_URL: dict[str, Any] = {
    "summary": "Generate QuickBooks OAuth connect URL",
    "description": (
        "Creates a QuickBooks OAuth authorization URL and one-time state token "
        "for authorized admin connection and reconnection flows. "
        "All operations run under a global singleton QuickBooks integration."
    ),
}

QB_CALLBACK: dict[str, Any] = {
    "summary": "Handle QuickBooks OAuth callback",
    "description": (
        "Completes QuickBooks OAuth code exchange, validates state, and persists encrypted "
        "access/refresh tokens for the global QuickBooks connection."
    ),
}

QB_STATUS: dict[str, Any] = {
    "summary": "Get QuickBooks connection and sync health status",
    "description": (
        "Returns QuickBooks connection details for the global integration, including connection lifecycle state "
        "(`active`, `expired`, `revoked`), token expiry, latest successful sync timestamp, and "
        "recent failed sync count for operational monitoring. ACL permission is required."
    ),
}

QB_DISCONNECT: dict[str, Any] = {
    "summary": "Disconnect QuickBooks integration",
    "description": "Marks the global QuickBooks connection inactive so future sync calls require reconnect. No request body is required.",
}

QB_SYNC_CUSTOMER: dict[str, Any] = {
    "summary": "Queue QuickBooks customer sync",
    "description": (
        "Queues a customer upsert to QuickBooks. Path: organization_id (tenant UUID). "
        "Body optional — see examples."
    ),
    **_QB_SYNC_BODY,
}

QB_SYNC_INVOICE: dict[str, Any] = {
    "summary": "Queue QuickBooks invoice sync",
    "description": (
        "Queues invoice sync for organization_id + invoice_id. Syncs line items, PrivateNote (internal notes + order ref), "
        "and re-applies linked credit notes in QuickBooks when force=true."
    ),
    **_QB_SYNC_BODY,
}

QB_SYNC_CREDIT_NOTE: dict[str, Any] = {
    "summary": "Queue QuickBooks credit note sync",
    "description": "Queues a credit memo upsert for credit_note_id under the organization.",
    **_QB_SYNC_BODY,
}

QB_SYNC_PAYMENT: dict[str, Any] = {
    "summary": "Queue QuickBooks payment sync",
    "description": (
        "Queues billing-payment sync. Requires positive allocations; posts QuickBooks Payment linked to mapped invoices."
    ),
    **_QB_SYNC_BODY,
}

QB_MAPPINGS_LIST: dict[str, Any] = {
    "summary": "List QuickBooks reference mappings",
    "description": "Returns global QuickBooks mapping records with optional filters by mapping type and active status.",
}

QB_MAPPINGS_UPSERT: dict[str, Any] = {
    "summary": "Upsert QuickBooks reference mapping",
    "description": "Creates or updates one global mapping from local key to QuickBooks reference id.",
    **request_body_openapi(
        example={
            "qb_ref_id": "61",
            "qb_ref_name": "Services",
            "is_active": True,
            "metadata": {"source": "manual_admin_mapping"},
        }
    ),
}

QB_MAPPINGS_DEACTIVATE: dict[str, Any] = {
    "summary": "Deactivate QuickBooks reference mapping",
    "description": "Soft-deactivates an existing mapping so it is excluded from strict mapping resolution.",
}

QB_SETTINGS_GET: dict[str, Any] = {
    "summary": "Get QuickBooks sync settings",
    "description": "Returns global QuickBooks sync policy settings used for validation, retries, and force behaviors.",
}

QB_SETTINGS_UPDATE: dict[str, Any] = {
    "summary": "Update QuickBooks sync settings",
    "description": "Updates global QuickBooks sync settings such as strict mapping mode and retry policy controls.",
    **request_body_openapi(
        example={
            "strict_mapping_mode": True,
            "sync_attachments": False,
            "auto_retry_enabled": True,
            "max_retry_attempts": 5,
            "retry_backoff_seconds": 120,
            "allow_force_reapply_credit": False,
        }
    ),
}

QB_VALIDATE_INVOICE: dict[str, Any] = {
    "summary": "Run invoice QuickBooks preflight validation",
    "description": "Performs validation checks and mapping readiness for an invoice without executing sync. No request body is required.",
}

QB_SYNC_HEALTH: dict[str, Any] = {
    "summary": "Get QuickBooks sync health metrics",
    "description": "Returns operational health indicators including failure counts, pending links, and last failure time.",
}

QB_RECONCILE: dict[str, Any] = {
    "summary": "Run QuickBooks reconciliation summary",
    "description": "Computes drift indicators across local entities and QuickBooks links for supported entity types.",
}

QB_RESYNC_ENTITY: dict[str, Any] = {
    "summary": "Queue targeted QuickBooks resync",
    "description": (
        "Queues a safe resync for one entity. Path params: organization_id, entity_type "
        "(customer | invoice | credit_note | payment), local_entity_id."
    ),
    **request_body_openapi(example={"force": False}),
}

QB_FAILURES_LIST: dict[str, Any] = {
    "summary": "List QuickBooks sync logs with filters",
    "description": (
        "Returns recent sync logs (all statuses by default) with optional filtering by status "
        "(repeat `status` for multiple values, e.g. `?status=FAILED&status=PENDING`), entity type "
        "(`customer`, `invoice`, `credit_note`, `credit_application`, `payment`), action, "
        "event type, error code, job id, and local entity id. Supports a free-text `search` query across "
        "job id, entity type, related QuickBooks id, and error code (case-insensitive partial match). "
        "Date filters (optional): preset `period` (`TODAY`, `LAST_7_DAYS`, `LAST_WEEK`, `LAST_30_DAYS`, "
        "`LAST_MONTH`) **or** custom inclusive `date_from` + `date_to` (UTC calendar days, max 366 days, "
        "`date_to` cannot be in the future). Omit all date params for no created_at window (recent logs by `limit` only). "
        "Action values are human-friendly (`Queued`, `Created`, `Updated`, `Deleted`, `No Change`, `Credit Applied`)."
    ),
}

QB_FAILURE_DETAIL: dict[str, Any] = {
    "summary": "Get QuickBooks sync log details",
    "description": "Returns one sync log record including payload context for support and audit troubleshooting.",
}

QB_RESYNC_BULK: dict[str, Any] = {
    "summary": "Queue bulk QuickBooks resync",
    "description": (
        "Queues resync for multiple logs selected by status and optional filters. "
        "Allowed filter values are documented in the request schema (status/statuses, entity_type, event_type, action). "
        "When no status filter is provided, defaults to both FAILED and PENDING logs. "
        "By default, FAILED logs are limited to retryable transient failures "
        "(set include_non_connection_failures=true to broaden scope). "
        "Use `resync/final-failures` when you only want retry-exhausted failed records."
    ),
    **request_body_openapi(
        examples={
            "failed_invoices": {
                "summary": "Retry FAILED invoice syncs",
                "value": {
                    "statuses": ["FAILED"],
                    "entity_type": "invoice",
                    "include_non_connection_failures": False,
                    "force": False,
                    "limit": 500,
                },
            },
            "pending_all": {
                "summary": "Replay all PENDING jobs",
                "value": {"statuses": ["PENDING"], "force": False},
            },
        }
    ),
}

QB_RESYNC_FINAL_FAILURES: dict[str, Any] = {
    "summary": "Queue QuickBooks resync for retry-exhausted failures",
    "description": (
        "Queues resync only for FAILED logs that are considered final failures "
        "(attempt_no >= worker max tries). Use when standard retries are exhausted."
    ),
    **request_body_openapi(
        example={
            "entity_type": "invoice",
            "event_type": "INVOICE_UPDATED",
            "action": "Updated",
            "force": True,
        }
    ),
}
