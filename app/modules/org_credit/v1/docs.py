from __future__ import annotations

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry

_ACCT_EXAMPLE = {
    "id": "acct-uuid",
    "organization_id": "org-uuid",
    "status": "ACTIVE",
    "status_reason": None,
    "action_by_user_id": None,
    "credit_limit": "50000.00",
    "credit_limit_updated_at": "2026-04-16T10:00:00Z",
    "pending_credit_limit": None,
    "pending_credit_limit_effective_from": None,
    "used_credit": "15000.00",
    "available_credit": "35000.00",
    "review_frequency": "QUARTERLY",
    "last_status_change_at": "2026-04-16T10:00:00Z",
    "credit_facility_start_date": "2026-01-01",
    "credit_facility_end_date": "2027-01-01",
    "payment_terms_days": 30,
    "pending_payment_terms_days": None,
    "pending_payment_terms_effective_from": None,
    "payment_terms_updated_at": "2026-04-10T10:00:00Z",
    "payment_terms_effective_from": "2026-04-10",
    "created_at": "2026-04-16T10:00:00Z",
    "updated_at": "2026-04-16T10:00:00Z",
}

_CREDIT_STATUS_MUTATION_EXAMPLE = {
    "status": "ON_HOLD",
    "last_changed_at": "2026-04-10T14:22:00Z",
    "reason": "Client missed two successive invoices",
    "action_by": {
        "id": "admin-uuid",
        "first_name": "Priya",
        "last_name": "Admin",
    },
}

_CREDIT_OVERVIEW_EXAMPLE = {
    "account": _ACCT_EXAMPLE,
    "utilization_percent": 30.0,
    "available_credit": "35000.00",
    "credit_status": {
        "status": "ACTIVE",
        "last_changed_at": "2026-04-16T10:00:00Z",
        "reason": None,
        "action_by": None,
    },
    "credit_limit": {
        "amount": "50000.00",
        "last_adjusted_at": "2026-04-10T09:00:00Z",
    },
    "credit_terms": {
        "payment_terms_days": 30,
        "terms_label": "Net 30",
    },
    "next_review": {
        "due_date": "2026-06-01",
        "days_remaining": 46,
    },
    "outstanding_balance": {
        "as_of": "2026-04-20T10:00:00Z",
        "total": "15000.00",
        "current": None,
        "unpaid_invoice_count": None,
        "overdue_portion": None,
    },
    "overdue": {
        "total": None,
        "overdue_invoice_count": None,
        "oldest_overdue_days": None,
    },
    "next_invoice": {
        "due_date": None,
        "days_until_due": None,
    },
    "internal_credit_score": {
        "score": 72,
        "label": "GOOD",
        "last_recalculated_at": "2026-04-18T10:00:00Z",
    },
    "report_summary": {
        "connect_id": "GB-0-12345678",
        "credit_score": 48,
        "credit_score_max": 100,
        "credit_rating": "B",
        "company_name": "Example Logistics Ltd",
        "last_checked_at": "2026-04-15T12:00:00Z",
    },
    "config_summary": {
        "approved_credit_limit": "50000.00",
        "credit_utilization_warning_pct": 80,
        "credit_clearance_period_days": 14,
        "allow_bookings_beyond_limit": False,
    },
    "credit_facility_end_date": "2027-01-01",
    "risk_flags": [],
}

_TREND_POINT_EXAMPLE = [
    {"period": "2026-01", "value": 50000.0, "change": None},
    {"period": "2026-04", "value": 55000.0, "change": 5000.0},
]


_CREDIT_ACCOUNT_OVERVIEW_EXAMPLE = {
    "status": "ACTIVE",
    "credit_limit": "10000.00",
    "outstanding_balance": "10000.00",
    "available_credit": "0.00",
    "credit_limit_used_percent": 100.0,
}


GET_ORG_CREDIT_ACCOUNT_OVERVIEW = create_doc_entry(
    summary="Get minimal credit account overview",
    description=(
        "Lean snapshot of the org's credit account for the order-creation UI: "
        "credit limit, current outstanding balance, available credit, and the "
        "utilisation percentage. Returns 404 when the org has no credit account "
        "so the caller can show a configure-credit-account banner."
    ),
    responses={
        200: success_entry("Credit account overview", data=_CREDIT_ACCOUNT_OVERVIEW_EXAMPLE),
        401: error_401_entry(),
        404: error_entry(
            "Credit account not configured",
            code="not_found",
            message="Credit account not configured for this organization.",
        ),
    },
)


GET_ORG_CREDIT_OVERVIEW = create_doc_entry(
    summary="Get credit overview KPIs",
    description=(
        "Structured snapshot for the credit Overview header: nested objects "
        "for status, limit, terms, next review, outstanding balance, internal "
        "score, and bureau/config summaries. Invoice-ageing and overdue "
        "breakdown fields are always returned but null until the payments "
        "module exists. Use sibling routes under /credit/overview/ for limit "
        "and utilisation trends and a short list of active alerts."
    ),
    responses={
        200: success_entry("Credit overview", data=_CREDIT_OVERVIEW_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

GET_ORG_CREDIT_OVERVIEW_LIMIT_TREND = create_doc_entry(
    summary="Get credit limit trend (overview)",
    description=(
        "Time series of applied credit limits from adjustment history. "
        "Query ``year`` (required), optional ``month`` (1–12) to restrict the "
        "window to one calendar month within that year, and ``granularity`` "
        "one of weekly, monthly, yearly, or daily. ``daily`` requires ``month``."
    ),
    responses={
        200: success_entry("Limit trend", data=_TREND_POINT_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
        422: error_entry("Validation failed", code="validation_error", message="Invalid trend parameters."),
    },
)

GET_ORG_CREDIT_OVERVIEW_UTILISATION_TREND = create_doc_entry(
    summary="Get credit utilisation trend (overview)",
    description=(
        "Utilisation percentage over time from the money-movement ledger "
        "(last snapshot per period bucket). Same query parameters as limit trend."
    ),
    responses={
        200: success_entry("Utilisation trend", data=_TREND_POINT_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
        422: error_entry("Validation failed", code="validation_error", message="Invalid trend parameters."),
    },
)

GET_ORG_CREDIT_OVERVIEW_ACTIVE_ALERTS = create_doc_entry(
    summary="List active credit alerts (overview preview)",
    description="Up to three most recently triggered active or snoozed alerts for the organisation.",
    responses={
        200: success_entry("Active alerts (max 3)", data=[]),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

GET_ORG_CREDIT_STATUS_HISTORY = create_doc_entry(
    summary="List credit account status changes",
    description=(
        "Paginated canonical timeline of credit account status transitions. "
        "Each row captures the from/to status, the reason, and the actor "
        "that performed the change. ``duration`` is whole days only (e.g. "
        "``14d``) from this row's ``created_at`` until the next status row for "
        "the same organisation (or until now for the latest row). Drives the "
        "\"View "
        "Full History\" list on the Overview tab."
    ),
    responses={
        200: success_entry(
            "Status history",
            data={
                "items": [
                    {
                        "id": "status-hist-uuid",
                        "from_status": "ACTIVE",
                        "to_status": "ON_HOLD",
                        "reason": "Client missed two successive invoices",
                        "duration": "12d",
                        "created_at": "2026-04-10T14:22:00Z",
                        "action_by": {
                            "id": "admin-uuid",
                            "first_name": "Priya",
                            "last_name": "Admin",
                        },
                    },
                ],
                "total": 1,
                "page": 1,
                "size": 20,
                "pages": 1,
            },
        ),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

POST_ORG_CREDIT_HOLD = create_doc_entry(
    summary="Place credit account on hold",
    description="Admin-only. Moves an active account to ON_HOLD with a categorised reason. Records an entry in status history.",
    responses={
        200: success_entry("Hold placed", data=_CREDIT_STATUS_MUTATION_EXAMPLE),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can perform this credit action."),
        422: error_entry("Validation failed", code="validation_error", message="Hold can only be placed on an active credit account."),
    },
)

POST_ORG_CREDIT_RELEASE_HOLD = create_doc_entry(
    summary="Release credit account hold",
    description="Admin-only. Returns an ON_HOLD account to ACTIVE. Records an entry in status history.",
    responses={
        200: success_entry("Hold released", data=_CREDIT_STATUS_MUTATION_EXAMPLE),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can perform this credit action."),
    },
)

POST_ORG_CREDIT_SUSPEND = create_doc_entry(
    summary="Suspend credit account",
    description="Admin-only. Suspends the credit wallet. Optionally triggers payment acceleration. Records an entry in status history.",
    responses={
        200: success_entry("Account suspended", data=_CREDIT_STATUS_MUTATION_EXAMPLE),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can perform this credit action."),
    },
)

POST_ORG_CREDIT_REACTIVATE = create_doc_entry(
    summary="Reactivate credit account",
    description="Admin-only. Reactivates a suspended or on-hold account to ACTIVE. Records an entry in status history.",
    responses={
        200: success_entry("Account reactivated", data=_CREDIT_STATUS_MUTATION_EXAMPLE),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can perform this credit action."),
    },
)

POST_ORG_CREDIT_CLOSE = create_doc_entry(
    summary="Close credit account",
    description="Admin-only. Permanently closes a credit account with a categorised reason. Requires confirmation text 'CLOSE'. Records an entry in status history.",
    responses={
        200: success_entry("Account closed", data=_CREDIT_STATUS_MUTATION_EXAMPLE),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can perform this credit action."),
        422: error_entry("Validation failed", code="validation_error", message="Credit account is already closed."),
    },
)

GET_CREDIT_ACTIVITY = create_doc_entry(
    summary="Get recent credit activity",
    description=(
        "Unified credit activity feed sourced from the central audit log. "
        "Returns ``CREDIT``-category entries for the organisation, newest "
        "first. Supports filtering by ``event_type`` (e.g. "
        "``CREDIT_LIMIT_ADJUSTED``, ``CREDIT_HOLD_TRIGGERED``, "
        "``CREDIT_TERMS_MODIFIED``, ``CREDIT_ALERT_SNOOZED``), "
        "``user_type`` (``Admin`` / ``Client`` / ``System``), ``severity`` "
        "(``INFO`` / ``NOTICE`` / ``WARNING`` / ``CRITICAL``), a date "
        "window, and a free-text ``search`` that matches the action, "
        "event type, reason, and actor email/name."
    ),
    responses={
        200: success_entry("Credit activity", data={
            "items": [
                {
                    "id": "audit-log-uuid",
                    "event_type": "CREDIT_HOLD_TRIGGERED",
                    "event_label": "Credit Hold Triggered",
                    "description": "Account placed on hold due to overdue balance",
                    "user_type": "System",
                    "severity": "CRITICAL",
                    "acted_by": None,
                    "acted_by_email": None,
                    "timestamp": "2026-04-15T14:10:00Z",
                    "audit_ref": "AUD-000123",
                    "entity_ref": None,
                    "entity_type": "org_credit_account",
                    "entity_id": "account-uuid",
                    "ip_address": "185.38.44.199",
                    "browser": "Chrome",
                    "device": "Desktop",
                    "os": "Windows 10",
                },
                {
                    "id": "audit-log-uuid-2",
                    "event_type": "CREDIT_LIMIT_ADJUSTED",
                    "event_label": "Credit Limit Adjusted",
                    "description": "Credit limit increased from £5,000 to £8,000",
                    "user_type": "Admin",
                    "severity": "NOTICE",
                    "acted_by": "Natalia James",
                    "acted_by_email": "natalia.james@swcouriers.example",
                    "timestamp": "2026-04-10T09:00:00Z",
                    "audit_ref": "AUD-000118",
                    "entity_ref": None,
                    "entity_type": "org_credit_account",
                    "entity_id": "account-uuid",
                    "ip_address": "213.205.143.12",
                    "browser": "Chrome",
                    "device": "Desktop",
                    "os": "macOS Sonoma",
                },
            ],
            "total": 2,
            "page": 1,
            "size": 20,
            "pages": 1,
        }),
        401: error_401_entry(),
    },
)
