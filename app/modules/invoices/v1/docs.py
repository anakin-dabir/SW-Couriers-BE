"""OpenAPI documentation snippets for Invoices v1 API.

Request bodies use realistic UUIDs and full field sets so Swagger/ReDoc matches FE contracts.
"""

from typing import Any

from app.core.swagger.utils import request_body_openapi

# ── Shared example IDs (copy into FE mocks) ───────────────────────────────────
_EX_ORG = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_EX_CUSTOMER = "8e4d5c6b-7a89-4012-b3c4-d5e6f7a8b9c0"
_EX_ORDER = "7f3c2a1b-4d5e-6f78-9012-3456789abcde"
_EX_INVOICE = "95259d07-e4e9-42c6-80f3-708aea874475"

_LINE_ITEM_SERVICE = {
    "description": "Same-day courier deliveries (May 2026)",
    "quantity": 1,
    "unit_price": "6000.00",
    "total_price": "6000.00",
    "line_type": "service",
}

_INVOICE_CREATE_BASE = {
    "organization_id": _EX_ORG,
    "customer_id": _EX_CUSTOMER,
    "order_id": _EX_ORDER,
    "billing_contact_email": "billing@urbannest.example",
    "issue_date": "2026-05-01",
    "due_date": "2026-05-31",
    "subtotal": "6000.00",
    "vat_rate": "20.00",
    "vat_amount": "1200.00",
    "total": "7200.00",
    "line_items": [_LINE_ITEM_SERVICE],
    "notes": "Customer requested manual invoice for this booking.",
}

INVOICES_LIST: dict[str, Any] = {
    "summary": "List invoices",
    "description": (
        "Paginated list with search (invoice number or order ID). "
        "Filter by status (invoice lifecycle) and/or payment_status using repeated list keys "
        "(e.g. status=SENT&status=DRAFT, payment_status=UNPAID&payment_status=OVERDUE). "
        "payment_status also supports portal filters REFUNDED (completed refunds on the invoice) and DISPUTED "
        "(allocated payment with dispute-like Braintree status). "
        "Date ranges and optional period shortcut. Validates invoiced_from <= invoiced_to, due_from <= due_to. "
        "Admin/SUPER_ADMIN can query across tenants, or pass organization_id to narrow to one org. "
        "CUSTOMER_B2B is restricted to JWT organization_id (query organization_id must match or is omitted). "
        "CUSTOMER_B2C is restricted to invoices where customer_id == current user. "
        "Admin can set show_draft=true. Optional sorting via sort_by and sort_order. "
        "Requires Resource.BILLING READ."
    ),
}

INVOICES_SUMMARY: dict[str, Any] = {
    "summary": "Invoices KPI summary",
    "description": (
        "Aggregate KPI counts for dashboard cards (total invoices, paid, unpaid, overdue, with_completed_refunds, with_open_disputes). "
        "Uses the same filter model as list endpoint (search, status, payment_status, date range, period). "
        "Read scope follows invoice access rules (admin cross-tenant or optional organization_id, B2B JWT org, B2C self). "
        "Requires Resource.BILLING READ."
    ),
}

INVOICES_CREATE: dict[str, Any] = {
    "summary": "Create invoice (draft or create & finalise)",
    "description": (
        "Create a new invoice. Use finalize=false for 'Save as Draft' (invoice_status DRAFT). "
        "Use finalize=true for 'Create & Finalise' (invoice_status SENT in one step). "
        "Assigns next INV-NNNNNN. One invoice per order enforced at finalize. "
        "Optional line_items[] must sum to subtotal within 0.02. "
        "notes sets the single INTERNAL NOTES field; ongoing edits use PUT /internal-note. Admin-only. Requires Resource.BILLING WRITE."
    ),
    **request_body_openapi(
        examples={
            "save_as_draft": {
                "summary": "Save as draft (full payload)",
                "value": {**_INVOICE_CREATE_BASE, "finalize": False},
            },
            "create_and_finalise": {
                "summary": "Create & finalise (queues QuickBooks sync when organization_id set)",
                "value": {**_INVOICE_CREATE_BASE, "finalize": True},
            },
            "minimal_draft": {
                "summary": "Minimal draft (no line items / notes)",
                "value": {
                    "organization_id": _EX_ORG,
                    "issue_date": "2026-05-01",
                    "due_date": "2026-05-31",
                    "subtotal": "100.00",
                    "vat_rate": "20.00",
                    "vat_amount": "20.00",
                    "total": "120.00",
                    "finalize": False,
                },
            },
        }
    ),
}

INVOICES_UPDATE: dict[str, Any] = {
    "summary": "Update draft invoice",
    "description": (
        "Partial update of a draft invoice (invoice_status DRAFT only). "
        "Cannot change lifecycle status here — use POST /finalize. "
        "Internal notes: use PUT /internal-note (not this endpoint). "
        "Include version from last GET for optimistic locking. Admin-only. Requires Resource.BILLING WRITE."
    ),
    **request_body_openapi(
        examples={
            "update_amounts": {
                "summary": "Update amounts and dates",
                "value": {
                    "issue_date": "2026-05-02",
                    "due_date": "2026-06-01",
                    "subtotal": "6500.00",
                    "vat_rate": "20.00",
                    "vat_amount": "1300.00",
                    "total": "7800.00",
                },
            },
            "assign_order": {
                "summary": "Link order and B2B customer",
                "value": {
                    "order_id": _EX_ORDER,
                    "organization_id": _EX_ORG,
                    "customer_id": _EX_CUSTOMER,
                    "billing_contact_email": "accounts@urbannest.example",
                },
            },
        }
    ),
}

INVOICES_INTERNAL_NOTE_GET: dict[str, Any] = {
    "summary": "Get invoice internal note",
    "description": (
        "Read the single INTERNAL NOTES field (invoices.notes). "
        "Response: invoice_id, notes (null when unset), has_note, invoice_status, updated_at, version. "
        "Also on invoice detail as notes. Administrator-only (ADMIN/SUPER_ADMIN). Requires Resource.BILLING READ."
    ),
}

INVOICES_INTERNAL_NOTE_CREATE: dict[str, Any] = {
    "summary": "Create invoice internal note",
    "description": (
        "Set invoices.notes when empty. Returns 409 if a note already exists. "
        "Prefer PUT /internal-note for Edit Note (upsert). Re-syncs QuickBooks PrivateNote when invoice is SENT. "
        "Administrator-only. Requires Resource.BILLING WRITE."
    ),
    **request_body_openapi(
        example={
            "notes": "Customer requested manual invoice for this booking.",
            "version": 1,
        }
    ),
}

INVOICES_INTERNAL_NOTE_UPDATE: dict[str, Any] = {
    "summary": "Upsert invoice internal note",
    "description": (
        "Create or replace internal note in one call (recommended for Edit Note). "
        "Idempotent when body matches current note. Requires invoice version (409 if stale). "
        "Re-syncs QuickBooks when SENT + organization_id. Administrator-only. Requires Resource.BILLING WRITE."
    ),
    **request_body_openapi(
        examples={
            "edit_note": {
                "summary": "Edit existing note",
                "value": {
                    "notes": "Customer requested manual invoice for this booking. Follow up 2026-05-15.",
                    "version": 3,
                },
            },
            "first_note_via_put": {
                "summary": "Create via PUT when notes empty",
                "value": {
                    "notes": "Customer requested manual invoice for this booking.",
                    "version": 1,
                },
            },
        }
    ),
}

INVOICES_INTERNAL_NOTE_DELETE: dict[str, Any] = {
    "summary": "Delete invoice internal note",
    "description": (
        "Clear invoices.notes. Idempotent when already empty. "
        "Query param version (invoice optimistic lock). Re-syncs QuickBooks when SENT. "
        "Administrator-only. Requires Resource.BILLING WRITE."
    ),
}

INVOICES_DELETE_DRAFT: dict[str, Any] = {
    "summary": "Delete draft invoice",
    "description": (
        "Permanently remove a draft invoice (invoice_status DRAFT). "
        "Cascades invoice events, line items, and PDF artifacts. "
        "Rejected when finalized or has payment allocations, applied credits, "
        "or is referenced as a credit-note reversal invoice. Admin-only. Requires Resource.BILLING WRITE."
    ),
}

INVOICES_GET: dict[str, Any] = {
    "summary": "Get invoice",
    "description": (
        "Get a single invoice by ID. Returns status (DRAFT | SENT) and payment_status (UNPAID, PARTIALLY_PAID, PAID, OVERDUE, VOID, WRITTEN_OFF). "
        "Admin/SUPER_ADMIN can read across tenants. CUSTOMER_B2B is organization-scoped. "
        "CUSTOMER_B2C is self-scoped by customer_id. Foreign-tenant/customer invoices return 404. "
        "Requires Resource.BILLING READ."
    ),
}

INVOICES_INVOICE_PAYMENTS: dict[str, Any] = {
    "summary": "List invoice payment history",
    "description": (
        "Paginated payment transactions allocated to this invoice. "
        "Useful for invoice detail payment history table. "
        "Read scope follows invoice access rules; foreign-tenant/customer invoice IDs return 404."
    ),
}

INVOICES_GET_DETAIL: dict[str, Any] = {
    "summary": "Get invoice detail",
    "description": (
        "Full invoice detail with status (invoice lifecycle), payment_status, KPIs (total amount, amount paid, outstanding balance, payment method), "
        "activity events, applied credit notes, line_items[], billing_contact_email, notes (internal), refund_summary, has_open_dispute. "
        "Requires Resource.BILLING READ."
    ),
}

INVOICES_FINALIZE: dict[str, Any] = {
    "summary": "Finalize invoice",
    "description": (
        "Set status from DRAFT to SENT. Idempotent if already SENT. "
        "Queues QuickBooks invoice sync when organization_id is set. Admin-only. Requires Resource.BILLING WRITE."
    ),
}

INVOICES_VOID: dict[str, Any] = {
    "summary": "Void invoice",
    "description": (
        "Mark invoice as VOID via immutable invoice event. Reason is required (non-blank) and recorded in audit and events. "
        "Admin-only. Requires Resource.BILLING WRITE."
    ),
    **request_body_openapi(
        example={
            "reason": "Customer dispute — duplicate billing for order SWC-ORD-009001",
        }
    ),
}

INVOICES_WRITE_OFF: dict[str, Any] = {
    "summary": "Write off invoice",
    "description": (
        "Mark invoice as WRITTEN_OFF via immutable invoice event. Reason is required (non-blank) and recorded. "
        "Admin-only. Requires Resource.BILLING WRITE."
    ),
    **request_body_openapi(
        example={
            "reason": "Uncollectable after 90 days — approved by finance",
        }
    ),
}

INVOICES_PDF_REQUEST: dict[str, Any] = {
    "summary": "Request PDF",
    "description": (
        "Request PDF generation. If a READY artifact exists for current data, returns it; "
        "otherwise creates artifact and enqueues job. Poll GET .../pdf for status. "
        "Read scope follows invoice access rules (admin cross-tenant, B2B organization, B2C self/customer_id). "
        "Foreign-tenant/customer requests return 404. Requires Resource.BILLING READ."
    ),
}

INVOICES_PDF_STATUS: dict[str, Any] = {
    "summary": "Get PDF status",
    "description": (
        "Current PDF generation status for polling (NOT_REQUESTED, GENERATING, READY, FAILED). "
        "Read scope follows invoice access rules (admin cross-tenant, B2B organization, B2C self/customer_id). "
        "Foreign-tenant/customer requests return 404. Requires Resource.BILLING READ."
    ),
}

INVOICES_PDF_SIGNED_URL: dict[str, Any] = {
    "summary": "Get signed URL",
    "description": (
        "Get a short-lived signed URL for the latest READY PDF. "
        "Request body disposition controls browser behavior: inline opens PDF viewer, attachment downloads the file. "
        "Returns 404 if no READY artifact or if invoice is outside caller tenant scope. "
        "Read scope follows invoice access rules (admin cross-tenant, B2B organization, B2C self/customer_id). "
        "Requires Resource.BILLING READ."
    ),
    **request_body_openapi(
        examples={
            "view_inline": {
                "summary": "Open in browser",
                "value": {"disposition": "inline"},
            },
            "download": {
                "summary": "Download file",
                "value": {"disposition": "attachment"},
            },
        }
    ),
}
