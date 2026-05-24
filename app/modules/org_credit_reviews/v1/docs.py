from __future__ import annotations

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry

_REVIEW_EXAMPLE = {
    "id": "review-uuid",
    "organization_id": "org-uuid",
    "account_id": "acct-uuid",
    "reviewer": {"id": "user-uuid", "first_name": "John", "last_name": "Smith"},
    "review_date": "2026-04-16",
    "review_frequency_at_time": "QUARTERLY",
    "risk_level": "LOW",
    "outcome": "MAINTAIN_CURRENT_TERMS",
    "review_notes": "All metrics healthy, maintaining current terms.",
    "next_review_frequency": "QUARTERLY",
    "recommended_new_limit": None,
    "recommended_payment_terms_days": None,
    "created_at": "2026-04-16T10:00:00Z",
    "updated_at": "2026-04-16T10:00:00Z",
}

_REVIEW_DETAIL_EXAMPLE = {**_REVIEW_EXAMPLE, "creditsafe": None}

_HISTORY_ITEM_EXAMPLE = {
    "id": "review-uuid",
    "review_date": "2026-04-16",
    "review_frequency_at_time": "QUARTERLY",
    "reviewer": {"id": "user-uuid", "first_name": "John", "last_name": "Smith"},
    "risk_level": "LOW",
    "outcome": "MAINTAIN_CURRENT_TERMS",
    "review_notes": "All metrics healthy, maintaining current terms.",
}

_ACCOUNT_EXAMPLE = {
    "id": "acct-uuid",
    "organization_id": "org-uuid",
    "status": "ACTIVE",
    "credit_limit": "50000.00",
    "credit_limit_updated_at": "2026-04-16T10:00:00Z",
    "pending_credit_limit": None,
    "pending_credit_limit_effective_from": None,
    "used_credit": "15000.00",
    "available_credit": "35000.00",
    "review_frequency": "QUARTERLY",
    "review_risk_level": "LOW",
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


_REVIEWS_STATUS_EXAMPLE = {
    "snapshot": {
        "status": "ACTIVE",
        "credit_limit": "50000.00",
        "last_review_date": "2026-03-16",
        "utilization_percent": 30.0,
        "next_review_due": "2026-06-16",
        "risk_level": "LOW",
    },
}

GET_ORG_CREDIT_REVIEWS_AND_STATUS = create_doc_entry(
    summary="Get organisation credit snapshot for reviews",
    description=(
        "`GET .../credit/reviews/summary`. Returns a single snapshot derived only from `org_credit_accounts`: "
        "status, limit, last/next review dates, utilisation, and `review_risk_level` (updated when a review is submitted)."
    ),
    responses={
        200: success_entry("Credit snapshot", data=_REVIEWS_STATUS_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

GET_ORG_CREDIT_REVIEWS_HISTORY = create_doc_entry(
    summary="List credit review history",
    description=(
        "`GET .../credit/reviews-history`. Paginated review rows for the table: date, review type (frequency at time), "
        "reviewer, risk level, outcome, and notes."
    ),
    responses={
        200: success_entry("Review history", data={"items": [_HISTORY_ITEM_EXAMPLE], "total": 1, "page": 1, "size": 20, "pages": 1}),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

GET_ORG_CREDIT_REVIEW_DETAIL = create_doc_entry(
    summary="Get credit review detail",
    description=(
        "Returns a single credit review by ID, including reviewer and recommendation fields. "
        "`creditsafe` is the same shape as `CreditReportResponse` from org credit (Creditsafe snapshot): "
        "prefer the report linked at review time (`credit_report_snapshot_id`); if none, falls back to the org’s latest report. "
        "`null` when no report exists."
    ),
    responses={
        200: success_entry("Review detail", data=_REVIEW_DETAIL_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Review not found", code="not_found", message="Credit review not found."),
    },
)

POST_ORG_CREDIT_REVIEW = create_doc_entry(
    summary="Submit a credit review",
    description=(
        "Performs a credit review for the organisation. Records risk assessment, outcome, and optional "
        "limit/terms recommendations. Updates `review_risk_level` and schedule fields on the org credit account. "
        "Does not change account lifecycle status automatically. "
        "Optional `credit_report_id` (36-char UUID): ties the review to that org Creditsafe snapshot; if omitted, "
        "the current org credit report is used when one exists."
    ),
    responses={
        201: success_entry("Review submitted", message="Credit review submitted."),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can perform this credit action."),
        422: error_entry(
            "Validation failed",
            code="validation_error",
            message="No credit account, invalid body (e.g. outcome requires recommended_new_limit or recommended_payment_terms_days), unknown credit_report_id for this organisation, or other validation error.",
        ),
    },
)

PATCH_ORG_CREDIT_REVIEW_CONFIGURATION = create_doc_entry(
    summary="Update review configuration",
    description=(
        "`PATCH .../credit/reviews/configuration`. Admin-only. Requires an existing org credit account. Request body must include all of: review_frequency, next_review_date (ISO date), "
        "reminder_period (THREE_DAYS, SEVEN_DAYS, FOURTEEN_DAYS), and reviewer_user_id (36-char user UUID)."
    ),
    responses={
        200: success_entry("Review configuration updated", data=_ACCOUNT_EXAMPLE, message="Review configuration updated."),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can perform this credit action."),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
        422: error_entry(
            "Validation failed",
            code="validation_error",
            message="No credit account for this organisation, or request body invalid (all fields required).",
        ),
    },
)
