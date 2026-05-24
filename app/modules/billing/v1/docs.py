from __future__ import annotations

from app.core.swagger import (
    create_doc_entry,
    error_401_entry,
    error_entry,
    error_validation_entry,
    success_entry,
)

# Shared example UUIDs for Swagger request bodies (FE fixtures)
_EX_ORG = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_EX_CUSTOMER = "8e4d5c6b-7a89-4012-b3c4-d5e6f7a8b9c0"
_EX_INVOICE = "95259d07-e4e9-42c6-80f3-708aea874475"
_EX_PAYMENT = "bf5eca49-ed0f-4e8b-95d8-7e51bd237af6"
_EX_CREDIT_NOTE = "c4e5f6a7-b8c9-4012-d3e4-f5a6b7c8d9e0"

_REMITTANCE_FILE_422 = error_validation_entry(
    "Validation failed (file type, size, or empty upload)",
    message="Remittance advice type 'image/webp' is not allowed. Accepted MIME types: application/pdf, image/jpeg, image/png",
    field="remittance_advice",
    field_message="Must be PNG, JPEG, or PDF (detected from file content), max 10 MB",
)

_STORAGE_500 = error_entry(
    "Object storage error",
    code="STORAGE_PROVIDER_ERROR",
    message="Storage provider error",
)

BILLING_PAYMENTS_LIST = create_doc_entry(
    "List payment history",
    {
        200: success_entry(
            "Paginated payment history",
            data={
                "items": [
                    {
                        "id": "payment-id",
                        "organization_id": "org-uuid",
                        "client_id": "SWC-ORG-00041",
                        "organization_reference": "SWC-ORG-00041",
                        "organization_trading_name": "UrbanNest Home",
                        "payment_number": "PAY-000001",
                        "amount": "50.00",
                        "status": "NOT_DEPOSITED",
                        "allocation_status": "UNALLOCATED",
                        "allocated_amount": "0.00",
                        "unallocated_amount": "50.00",
                        "payment_date": "2026-04-21",
                        "provider": "MANUAL",
                        "provider_txn_id": None,
                        "remittance_advice": None,
                        "allocations": [
                            {
                                "invoice_id": "invoice-id",
                                "invoice_number": "INV-000111",
                                "allocated_amount": "20.00",
                            }
                        ],
                        "qb_sync_status": "SYNCED",
                        "qb_last_sync_at": "2026-04-21T09:10:00Z",
                        "created_at": "2026-04-21T09:00:00Z",
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 20,
                "pages": 1,
            },
        ),
        401: error_401_entry(),
    },
    description=(
        "**Permissions:** ADMIN/SUPER_ADMIN require ``Resource.BILLING`` READ; CUSTOMER_B2B requires ``Resource.BILLING`` READ. "
        "Paginated payment history. All filters are **query parameters** (no request body). "
        "Repeat `status`, `allocation_status`, or `provider` for multi-select "
        "(e.g. `?status=PENDING&status=DEPOSITED&provider=MANUAL`). "
        "Optional `payment_date_from` / `payment_date_to`, `search`, `page`, `size`. "
        "VOIDED payments are excluded unless `status=VOIDED` is explicitly requested. "
        "Each item may include `remittance_advice` metadata when a file was uploaded. "
        "ADMIN and SUPER_ADMIN may omit ``organization_id`` to list payments across all organisations; "
        "each row includes ``organization_id``, ``client_id`` / ``organization_reference`` (e.g. SWC-ORG-*), "
        "and ``organization_trading_name``. Other callers remain org-scoped."
    ),
)

BILLING_PAYMENTS_OPTIONS = create_doc_entry(
    "Payment history filter options",
    {
        200: success_entry(
            "Allowed filter values",
            data={
                "statuses": ["DEPOSITED", "NOT_DEPOSITED", "PENDING", "WITHHELD_RETURNED"],
                "allocation_statuses": ["ALLOCATED", "PARTIALLY_ALLOCATED", "UNALLOCATED"],
                "providers": ["BRAINTREE", "MANUAL", "BANK_TRANSFER", "CHEQUE", "OTHER"],
            },
        ),
        401: error_401_entry(),
    },
    description="Returns enum values for payment history list/KPI query filters.",
)

BILLING_PAYMENTS_KPIS = create_doc_entry(
    "Payment history KPIs",
    {
        200: success_entry(
            "KPI summary for selected date range",
            data={
                "total_received": "24500.00",
                "allocated": "22300.00",
                "unallocated": "2200.00",
                "pending": "500.00",
            },
        ),
        401: error_401_entry(),
    },
    description=(
        "**Permissions:** ADMIN/SUPER_ADMIN require ``Resource.BILLING`` READ; CUSTOMER_B2B requires ``Resource.BILLING`` READ. "
        "Returns summary cards for payment history view. Accepts the same **query** filters as "
        "`GET /payments/history` (`status`, `allocation_status`, `provider`, dates, `search`). "
        "ADMIN and SUPER_ADMIN may omit ``organization_id`` to aggregate KPIs across all organisations."
    ),
)

BILLING_PAYMENTS_INVOICE_CANDIDATES = create_doc_entry(
    "List invoice allocation candidates",
    {
        200: success_entry(
            "Paginated SENT invoices with balance due",
            data={
                "items": [
                    {
                        "invoice_id": "invoice-id",
                        "invoice_number": "INV-001212",
                        "issue_date": "2026-05-01",
                        "due_date": "2026-05-31",
                        "payment_status": "PARTIALLY_PAID",
                        "balance_due": "87.50",
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 20,
                "pages": 1,
            },
        ),
        401: error_401_entry(),
        422: error_validation_entry(
            "Payer validation failed",
            message="customer_id is required for CUSTOMER_B2C invoice candidates",
            field="customer_id",
            field_message="Required when client_type is CUSTOMER_B2C; for admin B2B org-wide mode this field may be omitted.",
        ),
    },
    description=(
        "Lists **SENT** invoices for the organisation (and optional customer filter) that are suitable for payment allocation "
        "(`UNPAID`, `PARTIALLY_PAID`, or **derived** `OVERDUE`). "
        "Each row includes **`balance_due`** (invoice total minus applied credits and payment allocations). "
        "Pass **`organization_id`** when using an **ADMIN** or **SUPER_ADMIN** token (query param) — same "
        "organisation as the client you are recording against; payer validation is scoped to this org for B2B. "
        "**`customer_id`** is the payer user UUID (same as invoice `customer_id`, i.e. a `users.id`; **not** organisation id). "
        "For ADMIN/SUPER_ADMIN with **`client_type=CUSTOMER_B2B`**, this filter is optional (org-wide candidates). "
        "For non-admin callers and for **`CUSTOMER_B2C`**, **`customer_id`** remains required. "
        "Requires **`client_type`** "
        "(`CUSTOMER_B2B` / `CUSTOMER_B2C`, or shorthand **`B2B`** / **`B2C`**); "
        "when `customer_id` is provided, the customer must exist and match the client type (B2B users must belong to the scoped organisation). "
        "Pagination **`total`** and **`pages`** count only invoices with **positive** ``balance_due`` and exclude void / written-off outcomes."
    ),
)

BILLING_PAYMENTS_NOTES_PATCH = create_doc_entry(
    "Update payment notes",
    {
        200: success_entry(
            "Notes updated",
            data={
                "id": "payment-id",
                "payment_number": "PAY-000001",
                "notes": "Updated note text",
                "version": 3,
            },
        ),
        401: error_401_entry(),
        404: error_entry("Payment not found", code="NOT_FOUND", message="billing_payment with id '...' not found"),
        409: error_entry(
            "Conflict",
            code="CONFLICT",
            message="billing_payments was modified by another request.",
        ),
        422: error_validation_entry(
            "Notes too long",
            message="notes exceeds maximum length",
            field="notes",
            field_message="Maximum 500 characters",
        ),
    },
    description=(
        "Updates **`notes`** on a payment (max **500** characters). "
        "Optional **`version`** in the body enables optimistic locking (must match the current row `version`)."
    ),
    request_example={
        "notes": "Bank transfer ref HT-88421 — allocate to May invoices",
        "version": 2,
    },
)

BILLING_PAYMENTS_VOID = create_doc_entry(
    "Void payment",
    {
        200: success_entry(
            "Payment voided",
            data={
                "id": "payment-id",
                "payment_number": "PAY-000001",
                "status": "VOIDED",
                "allocation_status": "UNALLOCATED",
                "version": 3,
            },
        ),
        401: error_401_entry(),
        404: error_entry("Payment not found", code="NOT_FOUND", message="billing_payment with id '...' not found"),
        409: error_entry(
            "Conflict",
            code="CONFLICT",
            message="billing_payments was modified by another request.",
        ),
        422: error_validation_entry(
            "Cannot void allocated payment",
            message="Only unallocated payments can be voided",
            field="payment_id",
            field_message="Payment has allocations and cannot be voided",
        ),
    },
    description=(
        "Marks a payment as **`VOIDED`** (soft lifecycle change; no delete). "
        "Allowed only when the payment has **no allocations**. "
        "Optional **`version`** in body enables optimistic locking."
    ),
    request_example={
        "reason": "Duplicate payment entry — customer paid twice by mistake",
        "version": 2,
    },
)

BILLING_PAYMENTS_GET = create_doc_entry(
    "Get payment detail",
    {
        200: success_entry(
            "Payment detail",
            data={
                "id": "payment-id",
                "payment_number": "PAY-000001",
                "organization_id": "org-id",
                "customer_id": None,
                "recorded_by_id": "user-id",
                "amount": "50.00",
                "status": "NOT_DEPOSITED",
                "allocation_status": "PARTIALLY_ALLOCATED",
                "allocated_amount": "30.00",
                "unallocated_amount": "20.00",
                "payment_date": "2026-04-21",
                "provider": "MANUAL",
                "provider_txn_id": None,
                "notes": "Manual payment",
                "remittance_advice": {
                    "content_type": "application/pdf",
                    "original_filename": "remittance.pdf",
                    "size_bytes": 102400,
                    "uploaded_at": "2026-04-21T10:00:00+00:00",
                },
                "qb_sync_status": "NOT_SYNCED",
                "qb_last_sync_at": None,
                "created_at": "2026-04-21T09:00:00Z",
                "updated_at": "2026-04-21T09:05:00Z",
                "version": 2,
                "allocations": [
                    {
                        "invoice_id": "invoice-id",
                        "revision_no": 1,
                        "allocated_amount": "30.00",
                        "notes": "Initial allocation",
                        "created_at": "2026-04-21T09:05:00Z",
                        "invoice_number": "INV-000042",
                        "invoice_issue_date": "2026-04-01",
                        "invoice_total_amount": "120.00",
                        "invoice_remaining_amount": "90.00",
                    }
                ],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Payment not found", code="NOT_FOUND", message="billing_payment with id '...' not found"),
    },
    description=(
        "Get one payment with latest allocation breakdown per invoice and optional remittance advice metadata. "
        "Each allocation row includes **`invoice_number`**, **`invoice_issue_date`**, **`invoice_total_amount`**, "
        "and **`invoice_remaining_amount`** (outstanding after credits and all payment allocations)."
    ),
)

BILLING_PAYMENTS_CREATE = create_doc_entry(
    "Record payment",
    {
        201: success_entry("Payment recorded", data={"id": "payment-id", "payment_number": "PAY-000001"}),
        401: error_401_entry(),
        422: error_validation_entry(
            "Invalid body (e.g. amount ≤ 0)",
            message="amount must be greater than 0",
            field="amount",
            field_message="Must be greater than 0",
        ),
    },
    description=(
        "Record a canonical billing payment (JSON body). "
        "This endpoint is **org-scoped B2B**: requires **`client_type=CUSTOMER_B2B`** and matching `organization_id` scope. "
        "**`customer_id`** is optional/deprecated for this flow and ignored when provided. "
        "`CUSTOMER_B2C` is currently out of scope and returns validation error. "
        "**`notes`** are optional and limited to **500** characters. "
        "QuickBooks payment sync is queued only after the first positive **allocation**, not on create."
    ),
    request_example={
        "client_type": "CUSTOMER_B2B",
        "amount": "1250.00",
        "payment_date": "2026-05-15",
        "status": "NOT_DEPOSITED",
        "provider": "BANK_TRANSFER",
        "provider_txn_id": "HT-88421",
        "transaction_fee": "0.00",
        "notes": "May statement payment — unallocated",
    },
)

BILLING_PAYMENTS_CREATE_MULTIPART = create_doc_entry(
    "Record payment (multipart, optional remittance advice)",
    {
        201: success_entry(
            "Payment recorded (with optional allocations applied)",
            data={
                "id": "payment-id",
                "payment_number": "PAY-000001",
                "amount": "80.00",
                "allocated_amount": "50.00",
                "unallocated_amount": "30.00",
                "allocation_status": "PARTIALLY_ALLOCATED",
                "allocations": [
                    {
                        "invoice_id": "invoice-uuid-a",
                        "revision_no": 1,
                        "allocated_amount": "30.00",
                        "notes": "bulk in multipart",
                        "invoice_number": "INV-000101",
                        "invoice_issue_date": "2026-04-01",
                        "invoice_total_amount": "120.00",
                        "invoice_remaining_amount": "90.00",
                    }
                ],
            },
        ),
        401: error_401_entry(),
        422: _REMITTANCE_FILE_422,
        500: _STORAGE_500,
    },
    description=(
        "## Overview\n\n"
        "`POST /v1/billing/payments/multipart` records a canonical **billing payment** using "
        "`multipart/form-data`. You may attach an optional **remittance advice** file and optionally "
        "allocate the payment to one or more invoices **in the same request**.\n\n"
        "Create + allocate is **atomic**: if allocation validation fails, the payment row is not "
        "committed and any uploaded remittance file is removed from R2 (best-effort cleanup).\n\n"
        "**Auth / scope:** ADMIN/SUPER_ADMIN require ``Resource.BILLING`` WRITE; CUSTOMER_B2B requires ``Resource.BILLING`` WRITE. "
        "**ADMIN** / **SUPER_ADMIN** must pass query **`organization_id`** (UUID). "
        "**CUSTOMER_B2B** uses JWT tenant org (query `organization_id` must match or be omitted).\n\n"
        "---\n\n"
        "## Content type\n\n"
        "Send `Content-Type: multipart/form-data` with form fields below. "
        "Optional file part name: **`remittance_advice`**.\n\n"
        "---\n\n"
        "## Payment form fields\n\n"
        "| Field | Required | Type | Rules |\n"
        "|-------|----------|------|-------|\n"
        "| `amount` | Yes | decimal string | Must be **> 0** (GBP). |\n"
        "| `payment_date` | Yes | date (`YYYY-MM-DD`) | Payment date. |\n"
        "| `client_type` | Yes | enum string | `CUSTOMER_B2B`, `B2B`, `CUSTOMER_B2C`, or `B2C`. "
        "**Record payment currently requires org-scoped B2B:** use `CUSTOMER_B2B` / `B2B`. "
        "`CUSTOMER_B2C` / `B2C` returns **422** (`client_type` out of scope). |\n"
        "| `status` | No | enum | Default `NOT_DEPOSITED`. "
        "Allowed: `DEPOSITED`, `NOT_DEPOSITED`, `PENDING`, `WITHHELD_RETURNED`, `VOIDED`. "
        "Invalid value → **422** on field `status`. |\n"
        "| `provider` | No | enum | Default `MANUAL`. "
        "Allowed: `MANUAL`, `BANK_TRANSFER`, `CHEQUE`, `BRAINTREE`, `OTHER`. "
        "Invalid value → **422** on field `provider`. |\n"
        "| `provider_txn_id` | No | string | Max 255 chars. |\n"
        "| `transaction_fee` | No | decimal | Default `0`, must be **≥ 0**. |\n"
        "| `braintree_status` | No | string | Max 50 chars (Braintree flows). |\n"
        "| `notes` | No | string | Max **500** chars. |\n"
        "| `customer_id` | No | UUID string | **Deprecated / ignored** for B2B org-scoped record payment "
        "(stored only in payment metadata for audit). |\n"
        "| `organization_id` | Query only | UUID | Required for admin callers (see scope above). |\n\n"
        "---\n\n"
        "## Allocations at create time (optional)\n\n"
        "Omit all allocation fields to create an **unallocated** payment (`allocation_status=UNALLOCATED`).\n\n"
        "Use **exactly one** of the two styles below — **never both** in the same request "
        "(returns **422**, field `allocations_json`: *Mutually exclusive with single allocation fields*).\n\n"
        "### Style A — Single allocation (form fields)\n\n"
        "| Field | Required | Rules |\n"
        "|-------|----------|-------|\n"
        "| `allocation_invoice_id` | Yes* | Invoice UUID. Required if any single-allocation field is sent. |\n"
        "| `allocation_allocated_amount` | Yes* | Decimal **> 0**. Required if any single-allocation field is sent. |\n"
        "| `allocation_notes` | No | Max **2000** chars. |\n\n"
        "\\*If you send only `allocation_notes` without invoice/amount, **422** names the missing field.\n\n"
        "**Example (form fields):**\n"
        "```\n"
        "amount=50.00\n"
        "payment_date=2026-04-21\n"
        "client_type=CUSTOMER_B2B\n"
        "allocation_invoice_id=<invoice-uuid>\n"
        "allocation_allocated_amount=30.00\n"
        "allocation_notes=Initial allocation\n"
        "```\n\n"
        "### Style B — Single or bulk via `allocations_json`\n\n"
        "One form field containing a JSON string (not a nested multipart part).\n\n"
        "**Accepted shapes:**\n"
        "1. **Bulk wrapper:** `{\"allocations\": [{...}, {...}]}` — **1 to 100** items.\n"
        "2. **Bulk array:** `[{...}, {...}]` — same limits.\n"
        "3. **Single object:** `{\"invoice_id\": \"...\", \"allocated_amount\": \"30.00\", \"notes\": \"...\"}`\n\n"
        "Each allocation object:\n"
        "| Property | Required | Rules |\n"
        "|----------|----------|-------|\n"
        "| `invoice_id` | Yes | UUID string. |\n"
        "| `allocated_amount` | Yes | Decimal **> 0**. |\n"
        "| `notes` | No | Max **2000** chars. |\n\n"
        "**Example (bulk):**\n"
        "```json\n"
        "{\n"
        "  \"allocations\": [\n"
        "    {\"invoice_id\": \"<uuid-a>\", \"allocated_amount\": \"30.00\"},\n"
        "    {\"invoice_id\": \"<uuid-b>\", \"allocated_amount\": \"20.00\", \"notes\": \"Second invoice\"}\n"
        "  ]\n"
        "}\n"
        "```\n\n"
        "Invalid JSON → **422** (`allocations_json`, `json_invalid`). "
        "Invalid object shape → **422** on `allocations_json`.\n\n"
        "---\n\n"
        "## Allocation business rules (server-side)\n\n"
        "When one or more allocations are supplied, the service runs the same checks as "
        "`POST /v1/billing/payments/{payment_id}/allocations`:\n\n"
        "- Each `invoice_id` must exist and belong to the **same organization** as the payment.\n"
        "- Invoice **`status` must be `SENT`** (draft/void invoices cannot receive allocations).\n"
        "- **No duplicate `invoice_id`** in the same request.\n"
        "- Each `allocated_amount` must be **> 0**.\n"
        "- **Payment cap:** sum of new allocations + existing allocations on this payment "
        "must not exceed **`payment.amount`** → `PAYMENT_OVER_ALLOCATED` (**422**).\n"
        "- **Invoice cap:** each allocation must not exceed the invoice **outstanding balance** "
        "(invoice total minus applied credits minus all payment allocations) → "
        "`INVOICE_OVER_ALLOCATED` (**422**).\n"
        "- On success, payment `allocated_amount` / `unallocated_amount` / `allocation_status` "
        "are updated; invoice `paid_amount` / `payment_status` projections are recomputed.\n"
        "- **QuickBooks** payment sync is queued only after the first successful positive allocation.\n\n"
        "---\n\n"
        "## Remittance advice file (optional)\n\n"
        "Part name: **`remittance_advice`**. PNG, JPEG, or PDF only; **max 10 MB**. "
        "Content validated by **magic bytes** (not filename alone). "
        "Stored on **Cloudflare R2**; response includes metadata (`content_type`, `original_filename`, "
        "`size_bytes`, `uploaded_at`) — not the raw storage key.\n\n"
        "---\n\n"
        "## Response\n\n"
        "**201** — `BillingPaymentDetailResponse` including `allocations[]` with per-invoice "
        "`invoice_number`, `invoice_issue_date`, `invoice_total_amount`, `invoice_remaining_amount`.\n\n"
        "---\n\n"
        "## Common errors\n\n"
        "| HTTP | When |\n"
        "|------|------|\n"
        "| **422** | Validation (amount, enums, allocation rules, mixed allocation styles, bad JSON). "
        "`error.code` is typically `VALIDATION_ERROR`; see `error.details[].field`. |\n"
        "| **404** | Referenced `invoice_id` not found for the org (during allocation). |\n"
        "| **500** | R2 upload/storage failure (`STORAGE_PROVIDER_ERROR`). |\n"
    ),
)

BILLING_PAYMENTS_REMITTANCE_PUT = create_doc_entry(
    "Upload or replace remittance advice",
    {
        200: success_entry("Payment updated", data={"id": "payment-id"}),
        401: error_401_entry(),
        404: error_entry("Payment not found", code="NOT_FOUND", message="billing_payment with id '...' not found"),
        422: _REMITTANCE_FILE_422,
        500: _STORAGE_500,
    },
    description=(
        "**Multipart:** single file field `remittance_advice` (required). "
        "Replaces any existing advice after a successful upload; previous R2 object is deleted. "
        "Same file rules as record multipart (PNG / JPEG / PDF, 10 MB, content sniffing)."
    ),
)

BILLING_PAYMENTS_REMITTANCE_DELETE = create_doc_entry(
    "Remove remittance advice",
    {
        200: success_entry("Remittance advice removed", data={"id": "payment-id"}),
        401: error_401_entry(),
        404: error_entry(
            "Not found",
            code="NOT_FOUND",
            message="billing_payment_remittance_advice with id '...' not found",
        ),
        500: _STORAGE_500,
    },
    description="Deletes the remittance advice object from R2 and clears metadata on the payment.",
)

BILLING_PAYMENTS_REMITTANCE_SIGNED_URL = create_doc_entry(
    "Remittance advice signed URL (view or download)",
    {
        200: success_entry(
            "Short-lived URL",
            data={
                "url": "https://…",
                "expires_at": "2026-04-27T12:00:00+00:00",
                "content_type": "application/pdf",
                "disposition": "inline",
            },
        ),
        401: error_401_entry(),
        404: error_entry(
            "Not found",
            code="NOT_FOUND",
            message="billing_payment_remittance_advice with id '...' not found",
        ),
    },
    description=(
        "Returns a **presigned GET URL** (default **5 minutes**) for the stored object. "
        "Query **`disposition`**: `inline` (open in browser) or `attachment` (download with filename). "
        "Requires payment read access (ADMIN/SUPER_ADMIN: ``Resource.BILLING`` READ; CUSTOMER_B2B: ``Resource.BILLING`` READ) "
        "and correct `organization_id` scope (same as other billing payment routes)."
    ),
)

BILLING_PAYMENTS_ALLOCATE = create_doc_entry(
    "Add payment allocation",
    {
        200: success_entry("Allocation saved", data={"id": "payment-id", "allocation_status": "PARTIALLY_ALLOCATED"}),
        401: error_401_entry(),
        404: error_entry("Payment not found", code="NOT_FOUND", message="billing_payment with id '...' not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="allocated_amount cannot be negative"),
    },
    description=(
        "Append immutable additive allocation rows and recompute payment/invoice projections with over-allocation checks. "
        "The endpoint accepts **single** payload shape (`invoice_id`, `allocated_amount`, `notes`) "
        "or **bulk** payload shape (`allocations: [...]`) for multi-invoice allocation in one request; "
        "bulk operations are validated and applied atomically (no partial success). "
        "Returns payment detail including allocations with **`invoice_issue_date`**, **`invoice_total_amount`**, "
        "and **`invoice_remaining_amount`** per invoice row."
    ),
    request_examples={
        "single_allocation": {
            "summary": "Allocate to one invoice",
            "value": {
                "invoice_id": _EX_INVOICE,
                "allocated_amount": "500.00",
                "notes": "Partial allocation against INV-001212",
            },
        },
        "bulk_allocations": {
            "summary": "Allocate to multiple invoices",
            "value": {
                "allocations": [
                    {
                        "invoice_id": _EX_INVOICE,
                        "allocated_amount": "300.00",
                        "notes": "First invoice",
                    },
                    {
                        "invoice_id": "7aa2a95c-7e3e-4795-99f9-76f2b3804df7",
                        "allocated_amount": "200.00",
                        "notes": "Second invoice",
                    },
                ]
            },
        },
    },
)

BILLING_PAYMENTS_ALLOCATIONS_REPLACE = create_doc_entry(
    "Replace payment allocations",
    {
        200: success_entry("Allocations replaced", data={"id": "payment-id", "allocation_status": "ALLOCATED"}),
        401: error_401_entry(),
        404: error_entry("Payment not found", code="NOT_FOUND", message="billing_payment with id '...' not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="INVOICE_OVER_ALLOCATED: allocation exceeds invoice outstanding balance"),
    },
    description=(
        "Replace the payment's allocation snapshot atomically. "
        "Rows omitted from `allocations` are unallocated; `allocations: []` fully unallocates the payment. "
        "All payment/invoice projections are recomputed and QuickBooks payment sync is queued."
    ),
    request_examples={
        "replace_allocations": {
            "summary": "Replace full allocation set",
            "value": {
                "allocations": [
                    {
                        "invoice_id": _EX_INVOICE,
                        "allocated_amount": "750.00",
                        "notes": "Revised allocation",
                    }
                ]
            },
        },
        "clear_all": {
            "summary": "Unallocate payment completely",
            "value": {"allocations": []},
        },
    },
)

BILLING_PAYMENTS_ALLOCATIONS_REMOVE = create_doc_entry(
    "Remove allocation for one invoice",
    {
        200: success_entry("Allocation removed", data={"id": "payment-id", "allocation_status": "PARTIALLY_ALLOCATED"}),
        401: error_401_entry(),
        404: error_entry("Payment not found", code="NOT_FOUND", message="billing_payment with id '...' not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="No existing positive allocation found for invoice on this payment"),
    },
    description=(
        "Unallocates one invoice from a payment by removing its net allocation (append-only revision preserved). "
        "Recomputes projections and queues QuickBooks payment sync."
    ),
)


BILLING_REFUNDS_LIST = create_doc_entry(
    "List refunds",
    {
        200: success_entry(
            "Paginated refunds",
            data={
                "items": [
                    {
                        "id": "8f0ea709-c568-4d74-90f8-bf6124f7f8d2",
                        "refund_number": "REF-000048",
                        "payment_id": "bf5eca49-ed0f-4e8b-95d8-7e51bd237af6",
                        "payment_number": "PAY-2024-0866",
                        "invoice_id": "7aa2a95c-7e3e-4795-99f9-76f2b3804df7",
                        "invoice_number": "INV-1052",
                        "linked_booking_ref": "BK-7799",
                        "refund_date": "2026-03-25T10:15:00Z",
                        "amount": "1500.00",
                        "refund_type": "PARTIAL",
                        "refund_method": "CARD_REFUND",
                        "status": "PROCESSING",
                        "reason_category": "BILLING_ERROR",
                        "braintree_transaction_id": "BT-TXN-99713",
                        "braintree_status": "SETTLING",
                        "processed_by_id": None,
                        "completed_at": None,
                    }
                ],
                "total": 86,
                "page": 1,
                "size": 20,
                "pages": 5,
            },
        ),
        401: error_401_entry(),
    },
    description="Refund ledger list with search, date range, and multi-select filters (status/type/method/reason category).",
)

BILLING_REFUNDS_KPIS = create_doc_entry(
    "Refund KPIs",
    {
        200: success_entry(
            "Refund KPI cards",
            data={
                "total_refund_amount": "12600.00",
                "refunds_this_month": 10,
                "pending_refunds": 5,
                "failed_refunds": 1,
                "avg_refund_time_days": 3,
            },
        ),
        401: error_401_entry(),
    },
    description="Refund KPI summary for selected filter/date scope.",
)

BILLING_REFUNDS_OPTIONS = create_doc_entry(
    "Refund filter options",
    {200: success_entry("Refund enums", data={"statuses": [], "refund_types": [], "refund_methods": [], "reason_categories": []}), 401: error_401_entry()},
    description="Returns enum options for filter dropdowns/chips.",
)

BILLING_REFUNDS_GET = create_doc_entry(
    "Get refund detail",
    {
        200: success_entry(
            "Refund detail",
            data={
                "refund": {
                    "id": "8f0ea709-c568-4d74-90f8-bf6124f7f8d2",
                    "refund_number": "REF-000048",
                    "organization_id": "9af8bf75-2c5e-4d7b-a46f-5ab3290dfa1c",
                    "billing_payment_id": "bf5eca49-ed0f-4e8b-95d8-7e51bd237af6",
                    "invoice_id": "7aa2a95c-7e3e-4795-99f9-76f2b3804df7",
                    "linked_booking_ref": "BK-7799",
                    "provider": "BRAINTREE",
                    "refund_method": "CARD_REFUND",
                    "refund_type": "PARTIAL",
                    "status": "PROCESSING",
                    "reason_category": "BILLING_ERROR",
                    "reason_description": "Overcharged for premium tier; client was on standard plan.",
                    "requested_amount": "1500.00",
                    "processed_amount": "0.00",
                    "currency": "GBP",
                    "braintree_transaction_id": "BT-TXN-99713",
                    "braintree_status": "SETTLING",
                    "braintree_status_updated_at": "2026-03-25T10:16:00Z",
                    "retry_count": 0,
                    "failure_code": None,
                    "failure_message": None,
                    "initiated_by_id": "f3e140c5-1045-4c7d-a009-a388802f6fc3",
                    "processed_by_id": None,
                    "initiated_at": "2026-03-25T10:15:00Z",
                    "completed_at": None,
                    "metadata_json": {"source": "admin_portal"},
                    "created_at": "2026-03-25T10:15:00Z",
                    "updated_at": "2026-03-25T10:16:00Z",
                },
                "events": [
                    {
                        "id": "8be59f40-0f05-4cdc-b73c-7cdebc301f06",
                        "event_type": "INITIATED",
                        "actor_id": "f3e140c5-1045-4c7d-a009-a388802f6fc3",
                        "payload_json": {"requested_amount": "1500.00", "refund_method": "CARD_REFUND"},
                        "created_at": "2026-03-25T10:15:01Z",
                    }
                ],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Refund not found", code="NOT_FOUND", message="refund with id '...' not found"),
    },
    description="Returns one refund and its append-only event timeline.",
)

BILLING_REFUNDS_CREATE = create_doc_entry(
    "Initiate refund",
    {
        201: success_entry("Refund created", data={"id": "refund-id", "refund_number": "REF-000001"}),
        401: error_401_entry(),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Partial refund amount must be less than remaining refundable amount"),
    },
    description=(
        "Creates a refund row with strict amount/state validation and event logging. "
        "For CARD_REFUND, the source transaction id is resolved from the selected billing payment."
    ),
    request_example={
        "billing_payment_id": _EX_PAYMENT,
        "invoice_id": _EX_INVOICE,
        "linked_booking_ref": "BK-7799",
        "refund_type": "PARTIAL",
        "refund_method": "CARD_REFUND",
        "reason_category": "BILLING_ERROR",
        "reason_description": "Customer overcharged on May invoice — partial refund approved",
        "amount": "150.00",
        "metadata_json": {"ticket_id": "SUP-4421"},
    },
)

BILLING_REFUNDS_MARK_COMPLETE = create_doc_entry(
    "Mark refund complete",
    {
        200: success_entry("Refund marked complete", data={"id": "refund-id", "status": "COMPLETED"}),
        401: error_401_entry(),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Card refunds cannot be manually marked complete"),
    },
    description="Manual completion action (typically non-card flows).",
)

BILLING_REFUNDS_RETRY = create_doc_entry(
    "Retry failed refund",
    {
        200: success_entry("Refund retry queued", data={"id": "refund-id", "status": "PROCESSING"}),
        401: error_401_entry(),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Only failed refunds can be retried"),
    },
    description="Moves failed refund back into retry lifecycle and appends RETRIED event.",
)

BILLING_REFUNDS_ISSUE_CREDIT_NOTE = create_doc_entry(
    "Issue credit note for refund",
    {
        200: success_entry("Credit note issued for refund", data={"id": "refund-id", "status": "COMPLETED"}),
        401: error_401_entry(),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Only CREDIT_NOTE refunds can be issued as credit notes"),
    },
    description="Creates credit note and marks refund complete for credit-note method flows.",
)

BILLING_B2B_CREDIT_NOTES_LIST = create_doc_entry(
    "B2B: list credit notes",
    {
        200: success_entry(
            "Paginated credit notes",
            data={
                "items": [
                    {
                        "id": "credit-note-id",
                        "credit_note_number": "CN-000321",
                        "issue_date": "2026-05-07",
                        "total_credit_amount": "120.00",
                        "applied_amount": "20.00",
                        "remaining_amount": "100.00",
                        "status": "PARTIALLY_APPLIED",
                        "reason_category": "BILLING_ERROR",
                        "reason": "Invoice adjustment",
                        "source_invoice_id": "invoice-id",
                        "source_invoice_number": "INV-001212",
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 20,
                "pages": 1,
            },
        ),
        401: error_401_entry(),
    },
    description=(
        "Organisation-scoped credit note ledger (tenant from JWT `organization_id`). "
        "Optional `customer_id` query: omit for all org notes, UUID for one B2B customer, "
        "empty string for unassigned (`customer_id` IS NULL) rows only. "
        "Search, multi-select status/reason filters, date range, and sort. "
        "Available `status`: OPEN, PARTIALLY_APPLIED, FULLY_APPLIED, VOID. "
        "Common `reason_category`: BILLING_ERROR, SERVICE_FAILURE, CLIENT_REQUEST, OTHER. "
        "Sort fields: issue_date, amount, credit_note_number; sort order: asc, desc. "
        "Detail, apply, and PDF endpoints are org-scoped (same tenant, any contact may access)."
    ),
)

BILLING_B2B_CREDIT_NOTES_GET = create_doc_entry(
    "B2B: get credit note detail",
    {
        200: success_entry(
            "Credit note detail",
            data={
                "id": "credit-note-id",
                "credit_note_number": "CN-000321",
                "organization_id": "org-id",
                "customer_id": "user-id",
                "source_invoice_id": "invoice-id",
                "source_invoice_number": "INV-001212",
                "issue_date": "2026-05-07",
                "total_credit_amount": "120.00",
                "applied_amount": "20.00",
                "remaining_amount": "100.00",
                "status": "PARTIALLY_APPLIED",
                "reason_category": "BILLING_ERROR",
                "reason": "Invoice adjustment",
                "currency": "GBP",
                "sent_to_email": "billing@example.com",
                "sent_at": "2026-05-07T09:30:00Z",
                "applications": [
                    {
                        "invoice_id": "invoice-id",
                        "invoice_number": "INV-001212",
                        "applied_amount": "20.00",
                        "applied_at": "2026-05-07",
                    }
                ],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Credit note not found", code="NOT_FOUND", message="credit_note with id '...' not found"),
    },
    description="Returns one credit note with application timeline and remaining amount.",
)

BILLING_B2B_CREDIT_NOTES_CANDIDATES = create_doc_entry(
    "B2B: list eligible invoices",
    {
        200: success_entry(
            "Paginated invoice candidates",
            data={
                "items": [
                    {
                        "invoice_id": "invoice-id",
                        "invoice_number": "INV-001212",
                        "issue_date": "2026-05-01",
                        "due_date": "2026-05-31",
                        "payment_status": "PARTIALLY_PAID",
                        "outstanding_amount": "87.50",
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 20,
                "pages": 1,
            },
        ),
        401: error_401_entry(),
    },
    description=(
        "Lists outstanding invoices eligible for this credit note application. "
        "Typical `payment_status` values: UNPAID, PARTIALLY_PAID, OVERDUE."
    ),
)

BILLING_B2B_CREDIT_NOTES_APPLY = create_doc_entry(
    "B2B: apply credit note",
    {200: success_entry("Credit applied", data={"credit_note_id": "cn-id", "invoice_id": "inv-id", "applied_amount": "10.00", "applied_at": "2026-05-07"}), 401: error_401_entry(), 422: error_entry("Validation error", code="VALIDATION_ERROR", message="Nothing to apply")},
    description="Applies maximum eligible credit amount to the selected invoice in one transaction.",
    request_example={"invoice_id": _EX_INVOICE},
)

BILLING_B2B_CREDIT_NOTES_PDF_REQUEST = create_doc_entry(
    "B2B: request credit-note PDF",
    {200: success_entry("PDF generation requested", data={"status": "GENERATING", "artifact_id": "artifact-id", "job_id": "job-id"}), 401: error_401_entry()},
    description="Queues PDF generation for the current credit note signature (idempotent for matching content).",
)

BILLING_B2B_CREDIT_NOTES_PDF_STATUS = create_doc_entry(
    "B2B: credit-note PDF status",
    {200: success_entry("PDF status", data={"status": "READY", "artifact_id": "artifact-id"}), 401: error_401_entry()},
    description="Returns current PDF generation status for polling.",
)

BILLING_B2B_CREDIT_NOTES_PDF_SIGNED_URL = create_doc_entry(
    "B2B: credit-note PDF signed URL",
    {200: success_entry("Signed URL", data={"url": "https://...", "expires_at": "2026-05-07T10:00:00+00:00", "disposition": "attachment"}), 401: error_401_entry(), 404: error_entry("PDF not ready", code="NOT_FOUND", message="credit_note_pdf with id '...' not found")},
    description="Returns short-lived signed URL for the latest READY credit-note PDF artifact. `disposition` values: inline, attachment.",
)

BILLING_ADMIN_CREDIT_NOTES_LIST = create_doc_entry(
    "Admin: list credit notes",
    {
        200: success_entry(
            "Paginated credit notes for organisation",
            data={
                "items": [
                    {
                        "id": "credit-note-id",
                        "credit_note_number": "CN-000321",
                        "issue_date": "2026-05-07",
                        "total_credit_amount": "120.00",
                        "applied_amount": "20.00",
                        "remaining_amount": "100.00",
                        "status": "PARTIALLY_APPLIED",
                        "reason_category": "BILLING_ERROR",
                        "reason": "Invoice adjustment",
                        "source_invoice_id": "invoice-id",
                        "source_invoice_number": "INV-001212",
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 20,
                "pages": 1,
            },
        ),
        401: error_401_entry(),
        422: error_validation_entry(
            "organisation scope required",
            message="organization_id is required for this billing operation",
            field="organization_id",
            field_message="ADMIN and SUPER_ADMIN must pass organisation_id",
        ),
    },
    description=(
        "Paginated credit notes for one organisation. **Required** query `organization_id` (tenant UUID). "
        "Optional `search`, multi-select `status` and `reason_category`, `page`, `size`. "
        "Does not support B2B `customer_id` filtering — use the B2B list endpoint for client-portal filters."
    ),
)

BILLING_ADMIN_CREDIT_NOTES_GET = create_doc_entry(
    "Admin: get credit note detail",
    {
        200: success_entry(
            "Credit note detail",
            data={
                "id": "credit-note-id",
                "credit_note_number": "CN-000321",
                "organization_id": "org-id",
                "customer_id": "user-id",
                "applications": [],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Credit note not found", code="NOT_FOUND", message="credit_note with id '...' not found"),
        422: error_validation_entry(
            "organisation scope required",
            message="organization_id is required for this billing operation",
            field="organization_id",
            field_message="ADMIN and SUPER_ADMIN must pass organisation_id",
        ),
    },
    description=(
        "Returns one credit note with applications. **Required** query `organization_id` must match the credit note tenant."
    ),
)

BILLING_ADMIN_CREDIT_NOTES_CANDIDATES = create_doc_entry(
    "Admin: list eligible invoices for credit note",
    {
        200: success_entry(
            "Paginated invoice candidates",
            data={
                "items": [
                    {
                        "invoice_id": "invoice-id",
                        "invoice_number": "INV-001212",
                        "outstanding_amount": "87.50",
                        "payment_status": "PARTIALLY_PAID",
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 20,
                "pages": 1,
            },
        ),
        401: error_401_entry(),
        422: error_validation_entry(
            "organisation scope required",
            message="organization_id is required for this billing operation",
            field="organization_id",
            field_message="ADMIN and SUPER_ADMIN must pass organisation_id",
        ),
    },
    description=(
        "Lists outstanding SENT invoices eligible to receive this credit note. "
        "**Required** query `organization_id`. Customer is resolved from the credit note or its source invoice."
    ),
)

BILLING_ADMIN_CREDIT_NOTES_APPLY = create_doc_entry(
    "Admin: apply credit note to invoice",
    {
        200: success_entry(
            "Credit applied",
            data={
                "credit_note_id": "cn-id",
                "invoice_id": "inv-id",
                "applied_amount": "10.00",
                "applied_at": "2026-05-07",
            },
        ),
        401: error_401_entry(),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Nothing to apply"),
    },
    description=(
        "Applies the maximum eligible amount from the credit note to the selected invoice. "
        "**Required** query `organization_id`. Invoice customer must match the credit note customer "
        "(directly or via source invoice)."
    ),
    request_example={"invoice_id": _EX_INVOICE},
)

BILLING_ADMIN_CREDIT_NOTES_CREATE = create_doc_entry(
    "Admin: create credit note",
    {201: success_entry("Credit note created", data={"id": "credit-note-id"}), 401: error_401_entry()},
    description=(
        "Creates a new issued credit note for an organization. "
        "**Required** query `organization_id`. "
        "`customer_id` is optional when `source_invoice_id` is set (customer copied from invoice). "
        "Queues QuickBooks credit-note sync when org-linked."
    ),
    request_example={
        "organization_id": _EX_ORG,
        "source_invoice_id": _EX_INVOICE,
        "customer_id": _EX_CUSTOMER,
        "issue_date": "2026-05-10",
        "amount": "120.00",
        "reason_category": "BILLING_ERROR",
        "reason": "Overcharge on invoice INV-001212 — service not delivered",
    },
)

BILLING_ADMIN_CREDIT_NOTES_VOID = create_doc_entry(
    "Admin: void credit note",
    {
        200: success_entry(
            "Credit note voided (with reversal invoice when previously applied)",
            data={
                "id": _EX_CREDIT_NOTE,
                "credit_note_number": "CN-000321",
                "organization_id": _EX_ORG,
                "customer_id": _EX_CUSTOMER,
                "source_invoice_id": _EX_INVOICE,
                "source_invoice_number": "INV-001212",
                "reversal_invoice_id": "f1e2d3c4-b5a6-7890-abcd-ef1234567891",
                "reversal_invoice_number": "INV-001213",
                "issue_date": "2026-05-07",
                "total_credit_amount": "120.00",
                "applied_amount": "120.00",
                "remaining_amount": "0.00",
                "status": "VOID",
                "reason_category": "BILLING_ERROR",
                "reason": "Issued in error — full reversal",
                "currency": "GBP",
                "applications": [],
            },
        ),
        401: error_401_entry(),
        422: error_validation_entry(
            "organisation scope required",
            message="organization_id is required for this billing operation",
            field="organization_id",
            field_message="ADMIN and SUPER_ADMIN must pass organisation_id",
        ),
    },
    description=(
        "Voids a credit note. **Required** query `organization_id` and body **`reason`** (non-blank). "
        "When the credit note was **applied**, creates a **reversal invoice** (`reversal_invoice_id` / `reversal_invoice_number` "
        "on the response) and enqueues QuickBooks void + reversal sync chain. "
        "When never applied, voids in QuickBooks only. Idempotent if already VOID."
    ),
    request_example={
        "reason": "Issued in error — customer never received the disputed service",
    },
)

BILLING_ADMIN_CREDIT_NOTES_CLIENT_EMAIL = create_doc_entry(
    "Admin: get client email for credit note",
    {
        200: success_entry("Client email", data={"email": "client@example.com"}),
        401: error_401_entry(),
        422: error_validation_entry(
            "organisation scope required",
            message="organization_id is required for this billing operation",
            field="organization_id",
            field_message="ADMIN and SUPER_ADMIN must pass organisation_id",
        ),
    },
    description="Returns best-effort client email for send-to-client modal prefill. **Required** query `organization_id`.",
)

BILLING_ADMIN_CREDIT_NOTES_SEND = create_doc_entry(
    "Admin: send credit note to client",
    {
        200: success_entry("Credit note sent", data={"id": "credit-note-id"}),
        401: error_401_entry(),
        422: error_validation_entry(
            "organisation scope required",
            message="organization_id is required for this billing operation",
            field="organization_id",
            field_message="ADMIN and SUPER_ADMIN must pass organisation_id",
        ),
    },
    description="Sends a credit-note notification email to client and records send metadata. **Required** query `organization_id`.",
    request_examples={
        "use_prefill": {
            "summary": "Send to client email from profile",
            "value": {},
        },
        "override_email": {
            "summary": "Override recipient email",
            "value": {"email": "billing@urbannest.example"},
        },
    },
)
