from __future__ import annotations

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry

_COOLDOWN_EXAMPLE = {"months": 3, "days": 0, "hours": 0}

GET_GLOBAL_CREDIT_COOLDOWN = create_doc_entry(
    summary="Get global credit account cool-down period",
    description=(
        "Returns the effective cool-down period (months, days, hours) after applying the stored global "
        "configuration and falling back to the system default when nothing is configured."
    ),
    responses={
        200: success_entry("Global cool-down period", data=_COOLDOWN_EXAMPLE),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can view global credit settings."),
    },
)

PATCH_GLOBAL_CREDIT_COOLDOWN = create_doc_entry(
    summary="Update global credit account cool-down period",
    description=(
        "Admin-only. Sets the default cool-down applied to organisations that do not have their own override. "
        "When reset_to_defaults is true, send only that flag (no months, days, or hours). "
        "Otherwise send the months, days, and hours triplet with reset_to_defaults false."
    ),
    responses={
        200: success_entry("Global cool-down period updated", data=_COOLDOWN_EXAMPLE),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can update global credit settings."),
        422: error_entry(
            "Validation failed",
            code="validation_error",
            message="Do not send months, days, or hours when reset_to_defaults is true.",
        ),
    },
)

GET_ORG_CREDIT_COOLDOWN = create_doc_entry(
    summary="Get cool-down period for an organisation",
    description=(
        "Returns the effective cool-down period (months, days, hours) for this organisation after applying "
        "organisation override, global configuration, and system default in that order."
    ),
    responses={
        200: success_entry("Organisation cool-down period", data=_COOLDOWN_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

_ACTIVE_COOLDOWN_EXAMPLE = {
    "active": True,
    "ends_at": "2026-06-01T12:00:00+00:00",
    "remaining_seconds": 5184000,
    "summary": "Cool-down active until 2026-06-01T12:00:00+00:00 (60 days remaining)",
}

GET_ORG_ACTIVE_CREDIT_COOLDOWN = create_doc_entry(
    summary="Get active submission cool-down for an organisation",
    description=(
        "Returns whether a cool-down window is currently active (started internally after a qualifying event), "
        "when it ends, seconds remaining, and a short summary for display. If none is active or the window has "
        "expired, active is false and the other fields are null."
    ),
    responses={
        200: success_entry("Active cool-down status", data=_ACTIVE_COOLDOWN_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

_CREDIT_SETTINGS_EXAMPLE = {
    "credit_limit_section": {
        "total_limit": "50000.00",
        "available_credit": "35000.00",
        "utilisation_pct": 30.0,
        "credit_facility_start_date": "2026-01-01",
        "last_updated": "2026-04-16T10:00:00Z",
    },
    "credit_terms_section": {
        "payment_terms_days": 30,
        "last_updated": "2026-04-10T10:00:00Z",
    },
    "risk_controls_section": {"hold_threshold_pct": 80},
    "cooldown_section": {"months": 3, "days": 0, "hours": 0},
}

GET_CREDIT_SETTINGS = create_doc_entry(
    summary="Get credit settings",
    description=(
        "Returns composite credit settings including credit limit, payment terms, risk controls, and cool-down configuration. "
        "If the organisation has no `org_credit_account` row yet (e.g. credit not approved), limit/terms/risk fields are null or empty "
        "and `available_credit` is `0`; cool-down still reflects org/global/defaults."
    ),
    responses={
        200: success_entry("Credit settings", data=_CREDIT_SETTINGS_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

_TERMS_HISTORY_ITEM = {
    "id": "entry-uuid",
    "date": "2026-04-10T10:00:00Z",
    "old_terms": "14",
    "new_terms": "30",
    "effective_date": "2026-04-15",
    "modified_by": {
        "id": "user-uuid",
        "first_name": "John",
        "last_name": "Admin",
    },
    "reason": "Client requested extended terms",
    "applied_to_existing": False,
    "status": "APPLIED",
    "applied_at": "2026-04-10T10:00:00Z",
}

GET_TERMS_HISTORY = create_doc_entry(
    summary="Get payment terms modification history",
    description=(
        "Paginated list of payment terms changes for the organisation, newest first. "
        "Each row stores display labels and metadata recorded when terms were updated."
    ),
    responses={
        200: success_entry(
            "Terms history",
            data={
                "items": [_TERMS_HISTORY_ITEM],
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

_RISK_CONTROLS_EXAMPLE = {"hold_threshold_pct": 80}

GET_RISK_CONTROLS = create_doc_entry(
    summary="Get risk controls for an organisation",
    description="Returns the hold threshold percentage stored on the credit account.",
    responses={
        200: success_entry("Risk controls", data=_RISK_CONTROLS_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Account not found", code="not_found", message="Credit account not found."),
    },
)

PATCH_RISK_CONTROLS = create_doc_entry(
    summary="Update risk controls",
    description=(
        "Admin-only. Sets the hold threshold percentage on the credit account. "
        "Requires a credit account to already exist for the organisation. "
        "Changes are recorded in the audit log; no ledger entry is written. "
        "Response body is success plus message only."
    ),
    responses={
        200: success_entry("Risk controls updated", message="Risk controls updated."),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can perform this credit action."),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
        422: error_entry(
            "No credit account",
            code="validation_error",
            message="No credit account exists for this organisation. Credit must be assigned before risk controls can be updated.",
        ),
    },
)

_LIMIT_HISTORY_ITEM = {
    "id": "entry-uuid",
    "date": "2026-04-16T10:00:00Z",
    "previous_limit": "40000.00",
    "new_limit": "50000.00",
    "change_amount": "10000.00",
    "change_pct": "25.0%",
    "adjustment_type": "Increase",
    "effective_date": "2026-04-20",
    "updated_by": {
        "id": "user-uuid",
        "first_name": "John",
        "last_name": "Admin",
    },
    "reason_category": "BUSINESS_GROWTH",
    "justification": "Client expanding operations",
    "status": "APPLIED",
}

PATCH_ORG_CREDIT_LIMIT = create_doc_entry(
    summary="Adjust organisation credit limit",
    description=(
        "Admin-only. `PATCH .../credit/settings/adjust-limit`. Sets the operational credit limit with reason category, "
        "effective date, and justification. "
        "If the effective date is today or in the past, the limit applies immediately and an append-only history row "
        "is stored with status APPLIED. If the effective date is in the future, the pending limit is stored and the "
        "history row is SCHEDULED until the daily job applies it. No CREDIT_LIMIT_SET ledger entry is written. "
        "Response body is success plus message only."
    ),
    responses={
        200: success_entry("Credit limit updated", message="Credit limit updated."),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can perform this credit action."),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
        422: error_entry(
            "No credit account",
            code="validation_error",
            message="No credit account exists for this organisation.",
        ),
    },
)

GET_LIMIT_HISTORY = create_doc_entry(
    summary="Get credit limit adjustment history",
    description=(
        "Paginated list of credit limit adjustments from the append-only history table (newest first). "
        "Legacy ledger CREDIT_LIMIT_SET rows are not included."
    ),
    responses={
        200: success_entry(
            "Limit history",
            data={
                "items": [_LIMIT_HISTORY_ITEM],
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

PATCH_ORG_PAYMENT_TERMS = create_doc_entry(
    summary="Set payment terms",
    description=(
        "Admin-only. Sets payment terms as a number of days on an existing credit account. "
        "Requires a credit account to already exist for the organisation. "
        "If the effective date is in the future, pending fields are set until the daily scheduler applies them. "
        "Records the change in append-only terms history and the audit log. "
        "Applying new terms to existing unpaid invoices is handled by a separate process when enabled. "
        "Response body is success plus message only."
    ),
    responses={
        200: success_entry("Payment terms updated", message="Payment terms updated."),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can perform this credit action."),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
        422: error_entry(
            "No credit account",
            code="validation_error",
            message="No credit account exists for this organisation. Credit must be assigned before payment terms can be updated.",
        ),
    },
)

POST_ORG_CREDIT_COOLDOWN = create_doc_entry(
    summary="Set organisation cool-down period override",
    description=(
        "Admin-only. Creates or updates the organisation-specific cool-down override, or clears it when "
        "reset_to_defaults is true (send only that flag; no months, days, or hours). "
        "Otherwise send the triplet with reset_to_defaults false. Appends a COOLDOWN_PERIOD_SET ledger entry "
        "on the organisation credit account."
    ),
    responses={
        200: success_entry("Organisation cool-down updated", data=_COOLDOWN_EXAMPLE),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can perform this credit action."),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
        422: error_entry(
            "Validation failed",
            code="validation_error",
            message="Do not send months, days, or hours when reset_to_defaults is true.",
        ),
    },
)
