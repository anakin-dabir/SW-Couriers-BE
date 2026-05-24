"""OpenAPI documentation for account statements v1."""

from __future__ import annotations

from typing import Any

from app.core.swagger.utils import create_doc_entry, error_401_entry, error_entry, success_entry
from app.modules.account_statements.constants import COMPANY_ADDRESS, COMPANY_EMAIL, COMPANY_NAME

_PROVIDER = {
    "name": COMPANY_NAME,
    "address": COMPANY_ADDRESS,
    "email": COMPANY_EMAIL,
}

_AGING = {
    "days_1_30": "9210.00",
    "days_31_60": "9210.00",
    "days_61_90": "9210.00",
    "days_90_plus": "9210.00",
}

_LEDGER_ROW_INVOICE = {
    "row_type": "INVOICE",
    "reference_id": "00000000-0000-0000-0000-000000000010",
    "reference_number": "INV-1051",
    "issue_date": "2026-03-10",
    "payment_date": None,
    "order_ref": "SWC-BK-01234",
    "status": "UNPAID",
    "amount": "1200.00",
    "balance": "21850.00",
    "line_items": [
        {
            "description": "Same day courier",
            "quantity": 1,
            "unit_price": "1000.00",
            "total_price": "1000.00",
        }
    ],
}

_LEDGER_ROW_PAYMENT = {
    "row_type": "PAYMENT",
    "reference_id": "00000000-0000-0000-0000-000000000011",
    "reference_number": "PAY-000045",
    "issue_date": "2026-03-12",
    "payment_date": "2026-03-12",
    "order_ref": None,
    "status": "DEPOSITED",
    "amount": "-500.00",
    "balance": "21350.00",
    "line_items": [],
}

_LEDGER_SNAPSHOT = {
    "opening_balance": "20650.00",
    "closing_balance": "20650.00",
    "total_invoice_amount": "20650.00",
    "total_paid": "20650.00",
    "total_unpaid": "20650.00",
    "total_overdue": "20650.00",
    "aging": _AGING,
    "currency": "GBP",
    "truncated": False,
    "rows": [_LEDGER_ROW_INVOICE, _LEDGER_ROW_PAYMENT],
}

_STATEMENT_LIST_ITEM = {
    "id": "00000000-0000-0000-0000-000000000001",
    "statement_number": "ST-000001",
    "organization_id": "00000000-0000-0000-0000-000000000002",
    "period_start": "2025-09-01",
    "period_end": "2025-10-01",
    "opening_balance": "20650.00",
    "closing_balance": "20650.00",
    "pdf_status": "READY",
    "created_at": "2026-02-01T10:00:00Z",
    "created_by_user_type": "ADMIN",
    "created_by_user_id": None,
    "generated_at": "2026-02-01T10:01:00Z",
}

_STATEMENT_DETAIL = {
    **_STATEMENT_LIST_ITEM,
    "total_invoice_amount": "20650.00",
    "total_paid": "20650.00",
    "total_unpaid": "20650.00",
    "total_overdue": "20650.00",
    "aging": _AGING,
    "include_line_item_detail": True,
    "include_credit_notes": True,
    "include_payment_history": True,
    "provider": _PROVIDER,
    "client_name": "UrbanNest Home",
    "client_address": "55 Bridge End, Cardiff, CF10 2BN, United Kingdom",
    "client_email": "accounts@urbannesthome.co.uk",
    "snapshot": _LEDGER_SNAPSHOT,
    "failure_reason": None,
}

_PREVIEW_RESPONSE = {
    "organization_id": "00000000-0000-0000-0000-000000000002",
    "period_start": "2025-09-01",
    "period_end": "2025-10-01",
    "provider": _PROVIDER,
    "client_name": "UrbanNest Home",
    "client_address": "55 Bridge End, Cardiff, CF10 2BN, United Kingdom",
    "client_email": "accounts@urbannesthome.co.uk",
    "ledger": _LEDGER_SNAPSHOT,
}

_SUMMARY_RESPONSE = {
    "opening_balance": "20650.00",
    "closing_balance": "20650.00",
    "total_invoice_amount": "20650.00",
    "total_paid": "20650.00",
    "total_unpaid": "20650.00",
    "total_overdue": "20650.00",
    "aging": _AGING,
    "currency": "GBP",
    "truncated": False,
}

ACCOUNT_STATEMENTS_LIST: dict[str, Any] = create_doc_entry(
    summary="List account statements",
    description=(
        "Paginated statement history for a B2B organization. Supports search by statement number, "
        "filters on statement period and generated_at. Excludes soft-deleted rows. "
        "List rows show statement_number (ST-000001), period_start/period_end, and closing_balance. "
        "Requires Resource.BILLING READ (admin and B2B)."
    ),
    responses={
        200: success_entry("Statement list", data={"items": [_STATEMENT_LIST_ITEM], "total": 1, "page": 1, "size": 20}),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Insufficient permissions"),
    },
)

ACCOUNT_STATEMENTS_GET: dict[str, Any] = create_doc_entry(
    summary="Get account statement detail",
    description=(
        "Returns full statement metadata for the UI modal: provider letterhead, client name/address, "
        "summary totals, aging buckets, and frozen `snapshot` (ledger rows with optional line_items, "
        "running balance per row). Use this after POST generate. Row types: INVOICE, PAYMENT, "
        "CREDIT_NOTE, REFUND."
    ),
    responses={
        200: success_entry("Statement detail", data=_STATEMENT_DETAIL),
        401: error_401_entry(),
        404: error_entry("Not found", code="NOT_FOUND", message="Statement not found"),
    },
)

ACCOUNT_STATEMENTS_PREVIEW: dict[str, Any] = create_doc_entry(
    summary="Preview statement data (live ledger)",
    description=(
        "Read-only ledger preview for a date range before saving a statement. "
        "Query params: period_start, period_end, include_line_item_detail (invoice line_items[]), "
        "include_credit_notes, include_payment_history. "
        "Response `ledger` has the same shape as GET detail `snapshot`. "
        "Aging uses as-of today for live preview; generated statements freeze aging at period_end."
    ),
    responses={
        200: success_entry("Preview", data=_PREVIEW_RESPONSE),
        401: error_401_entry(),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Invalid period"),
    },
)

ACCOUNT_STATEMENTS_SUMMARY: dict[str, Any] = create_doc_entry(
    summary="Statement summary cards only",
    description=(
        "Lightweight opening/closing balances, footer totals, and aging buckets — no ledger table rows. "
        "Same query params as preview."
    ),
    responses={200: success_entry("Summary", data=_SUMMARY_RESPONSE), 401: error_401_entry()},
)

ACCOUNT_STATEMENTS_CREATE: dict[str, Any] = create_doc_entry(
    summary="Generate account statement",
    description=(
        "Creates a statement record, computes ledger snapshot (with line_items when "
        "include_line_item_detail=true), enqueues WeasyPrint PDF job, uploads to R2. "
        "Response matches GET detail. Idempotent when the same period and options already exist. "
        "Max period 366 days; period_end cannot be in the future."
    ),
    responses={
        201: success_entry("Statement created", data=_STATEMENT_DETAIL),
        401: error_401_entry(),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Invalid period"),
    },
)

ACCOUNT_STATEMENTS_PDF_STATUS: dict[str, Any] = create_doc_entry(
    summary="Poll statement PDF status",
    description="Poll until status is READY before requesting signed URL.",
    responses={
        200: success_entry("PDF status", data={"statement_id": "...", "status": "READY", "job_id": "job-1"}),
        401: error_401_entry(),
    },
)

ACCOUNT_STATEMENTS_SIGNED_URL: dict[str, Any] = create_doc_entry(
    summary="Get presigned PDF URL",
    description="Use disposition=inline for browser view or attachment for download. URL expires in 5 minutes.",
    responses={200: success_entry("Signed URL", data={"url": "https://...", "expires_at": "..."}), 401: error_401_entry()},
)

ACCOUNT_STATEMENTS_SEND_EMAIL: dict[str, Any] = create_doc_entry(
    summary="Email statement to client",
    description="Admin only. Sends download link after PDF is READY. Cannot delete statement after successful send.",
    responses={
        200: success_entry("Email sent", data={"recipient_email": "billing@client.com", "status": "SENT"}),
        401: error_401_entry(),
        409: error_entry("Conflict", code="CONFLICT", message="PDF not ready"),
    },
)

ACCOUNT_STATEMENTS_DELETE: dict[str, Any] = create_doc_entry(
    summary="Delete account statement",
    description="Soft-delete. Returns 409 if statement was already emailed.",
    responses={
        200: success_entry("Deleted", data={"id": "..."}),
        409: error_entry("Conflict", code="CONFLICT", message="Cannot delete after email sent"),
    },
)

ACCOUNT_STATEMENTS_SCHEDULES_LIST: dict[str, Any] = create_doc_entry(
    summary="List recurring statement schedules",
    responses={200: success_entry("Schedules", data=[]), 401: error_401_entry()},
)

ACCOUNT_STATEMENTS_SCHEDULES_CREATE: dict[str, Any] = create_doc_entry(
    summary="Create recurring statement schedule",
    description=(
        "MONTHLY_FIRST and QUARTERLY may omit ``valid_from`` / ``valid_to`` (ongoing from today in "
        "``timezone``). CUSTOM requires ``valid_from`` and ``valid_to``; ``interval_days`` is optional "
        "(one statement for the date range when omitted)."
    ),
    responses={201: success_entry("Schedule created", data={}), 401: error_401_entry()},
)
