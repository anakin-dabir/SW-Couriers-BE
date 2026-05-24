from __future__ import annotations

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry

GET_ORG_CREDIT_LEDGER = create_doc_entry(
    summary="List credit ledger entries",
    description="Paginated immutable ledger for the organisation credit wallet. Optional filter by movement type.",
    responses={
        200: success_entry("Ledger page", data={"items": [], "total": 0, "page": 1, "size": 20, "pages": 0}),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

GET_CREDITSAFE_REPORT = create_doc_entry(
    summary="Get CreditSafe report",
    description="Returns the full structured CreditSafe report for the organisation, including score, risk indicators, company information, directors, payment behaviour, and negative information.",
    responses={
        200: success_entry("CreditSafe report", data={
            "score_section": {"credit_score": 48, "credit_score_max": 100, "credit_rating": "B"},
            "company_information": {"company_name": "Example Ltd"},
        }),
        401: error_401_entry(),
        404: error_entry("Report not found", code="not_found", message="CreditSafe report not found."),
    },
)

POST_CREDITSAFE_RECALCULATE = create_doc_entry(
    summary="Recalculate CreditSafe score",
    description="Admin-only. Runs a fresh credit check against the CreditSafe API. Requires confirmation text 'RUN ANOTHER CREDIT CHECK'.",
    responses={
        200: success_entry("CreditSafe report recalculated", data={"score_section": {"credit_score": 52}}),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can perform this action."),
        422: error_entry("Validation failed", code="validation_error", message="No registration number available."),
    },
)

GET_INTERNAL_SCORE = create_doc_entry(
    summary="Get internal credit score",
    description=(
        "Returns the internally computed credit score (0–100), qualitative band label "
        "(EXCELLENT, GOOD, FAIR, POOR, VERY_POOR), last calculation date, and factor breakdown."
    ),
    responses={
        200: success_entry("Internal score", data={"current_score": 72, "label": "GOOD", "score_breakdown": {}}),
        401: error_401_entry(),
        404: error_entry("Account not found", code="not_found", message="Credit account not found."),
    },
)

POST_INTERNAL_SCORE_RECALCULATE = create_doc_entry(
    summary="Recalculate internal credit score",
    description="Admin-only. Recomputes the internal credit score from payment history, utilisation, account age, and bureau score.",
    responses={
        200: success_entry("Internal score recalculated", data={"current_score": 75, "label": "GOOD"}),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can perform this action."),
    },
)

_UTILISATION_EXAMPLE = {
    "current": {
        "current_utilisation_pct": 30.0,
        "utilisation_label": "Low",
        "credit_limit": "50000.00",
        "available_credit": "35000.00",
        "outstanding_balance": "15000.00",
        "hold_threshold_pct": 85,
    },
    "history": [],
    "history_total": 0,
    "payment_behaviour": {
        "summary": None,
        "risk_indicator": None,
        "trend": None,
    },
    "ageing": {
        "as_of": None,
        "total_outstanding": None,
    },
    "ageing_buckets": [
        {"label": "0-30", "amount": None, "share_pct": None},
        {"label": "31-60", "amount": None, "share_pct": None},
        {"label": "61-90", "amount": None, "share_pct": None},
        {"label": "90+", "amount": None, "share_pct": None},
    ],
}

GET_UTILISATION = create_doc_entry(
    summary="Get credit utilisation",
    description=(
        "Returns current utilisation metrics and a paginated history of utilisation snapshots from the ledger. "
        "Optional ``date_from`` and ``date_to`` (inclusive calendar dates, UTC day bounds) filter history rows by "
        "ledger ``created_at``. Payment-behaviour and ageing blocks are placeholders until the payments module "
        "supplies invoice-ageing data."
    ),
    responses={
        200: success_entry("Utilisation data", data=_UTILISATION_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Account not found", code="not_found", message="Credit account not found."),
    },
)

GET_CREDIT_LIMIT_TREND = create_doc_entry(
    summary="Get credit limit trend",
    description=(
        "Returns trend data points of credit limit changes over time. "
        "Query ``year`` and ``granularity`` (weekly, monthly, yearly, daily); "
        "optional ``month`` (1–12) limits the window to that calendar month. "
        "``daily`` requires ``month``."
    ),
    responses={
        200: success_entry("Credit limit trend", data=[{"period": "2026-04", "value": 50000.0}]),
        401: error_401_entry(),
        422: error_entry("Validation failed", code="validation_error", message="Invalid trend parameters."),
    },
)

GET_UTILISATION_TREND = create_doc_entry(
    summary="Get utilisation trend",
    description=(
        "Returns trend data points of credit utilisation percentage over time from the ledger. "
        "Same ``year``, ``month``, and ``granularity`` parameters as the credit limit trend."
    ),
    responses={
        200: success_entry("Utilisation trend", data=[{"period": "2026-04", "value": 30.0}]),
        401: error_401_entry(),
        422: error_entry("Validation failed", code="validation_error", message="Invalid trend parameters."),
    },
)

GET_INTERNAL_SCORE_TREND = create_doc_entry(
    summary="Get internal credit score trend",
    description=(
        "Returns trend data points of the internally computed credit score over time, filtered by year and granularity. "
        "Each point corresponds to a recalculation event and includes the band label for that score."
    ),
    responses={
        200: success_entry(
            "Internal score trend",
            data=[{"period": "2026-04", "value": 72.0, "change": 2.0, "label": "GOOD"}],
        ),
        401: error_401_entry(),
    },
)
