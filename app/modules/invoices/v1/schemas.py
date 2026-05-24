"""Pydantic schemas for Invoices v1 API.

Request/response and query schemas for invoice CRUD, lifecycle (draft/finalize), void, write-off,
and PDF (status, signed URL). All currency fields use CurrencyAmount (Decimal, 2 decimal places).
Status filters: invoice lifecycle (DRAFT | SENT) and payment/outcome (UNPAID, PAID, VOID, etc.).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from app.common.schemas import BaseSchema, CurrencyAmount, PaginationParams

# Literal types for API filters and responses (must match enums)
InvoiceStatusLiteral = Literal["DRAFT", "SENT"]
PaymentStatusLiteral = Literal[
    "UNPAID",
    "PARTIALLY_PAID",
    "PAID",
    "OVERDUE",
    "VOID",
    "WRITTEN_OFF",
    "REFUNDED",
    "DISPUTED",
]


class InvoiceLineItemCreateRequest(BaseSchema):
    description: str = Field(..., min_length=1, max_length=500)
    quantity: int = Field(default=1, ge=1)
    unit_price: CurrencyAmount = Field(..., ge=0)
    total_price: CurrencyAmount = Field(..., ge=0)
    line_type: str = Field(default="service", max_length=30)


class InvoiceCreateRequest(BaseSchema):
    """Create a new invoice (draft or create & finalise in one step)."""

    order_id: str | None = Field(default=None, description="Order to invoice. One invoice per order.")
    organization_id: str | None = Field(default=None, description="B2B organization.")
    customer_id: str | None = Field(default=None, description="Customer (user) id.")
    billing_contact_email: str | None = Field(default=None, max_length=255, description="Billing contact email for PDF/QB.")
    issue_date: date = Field(..., description="Invoice date.")
    due_date: date = Field(..., description="Due date.")
    subtotal: CurrencyAmount = Field(..., ge=0, description="Subtotal before VAT (2 decimal places).")
    vat_rate: CurrencyAmount = Field(default=Decimal("20.0"), ge=0, le=100, description="VAT rate percent (2 decimal places).")
    vat_amount: CurrencyAmount = Field(..., ge=0, description="VAT amount (2 decimal places).")
    total: CurrencyAmount = Field(..., ge=0, description="Total amount (2 decimal places).")
    line_items: list[InvoiceLineItemCreateRequest] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=2000, description="Internal notes (admin only).")
    finalize: bool = Field(default=False, description="If true, create and immediately finalise (invoice_status SENT). Single-step 'Create & Finalise'.")

    @model_validator(mode="after")
    def due_date_on_or_after_issue_date(self) -> InvoiceCreateRequest:
        if self.due_date < self.issue_date:
            raise ValueError("due_date must be on or after issue_date")
        return self

    @model_validator(mode="after")
    def line_items_match_subtotal(self) -> InvoiceCreateRequest:
        if not self.line_items:
            return self
        line_sum = sum((li.total_price for li in self.line_items), Decimal("0"))
        if abs(line_sum - self.subtotal) > Decimal("0.02"):
            raise ValueError("line_items total_price sum must match subtotal within 0.02")
        return self


class InvoiceUpdateRequest(BaseSchema):
    """Partial update for a draft invoice. Cannot set lifecycle status."""

    order_id: str | None = None
    organization_id: str | None = None
    customer_id: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    subtotal: CurrencyAmount | None = Field(default=None, ge=0)
    vat_rate: CurrencyAmount | None = Field(default=None, ge=0, le=100)
    vat_amount: CurrencyAmount | None = Field(default=None, ge=0)
    total: CurrencyAmount | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def due_date_on_or_after_issue_date(self) -> InvoiceUpdateRequest:
        if self.issue_date is not None and self.due_date is not None and self.due_date < self.issue_date:
            raise ValueError("due_date must be on or after issue_date")
        return self


class InvoiceListItem(BaseSchema):
    """Single invoice in list: table columns + invoice and payment status."""

    id: str
    invoice_number: str = Field(..., min_length=1, description="Human-readable invoice code (typically INV-NNNNNN).")
    order_reference: str | None = None
    invoiced_date: date
    due_date: date
    total: CurrencyAmount = Field(..., ge=0)
    paid: CurrencyAmount = Field(
        ...,
        ge=0,
        description="Cash/card payments allocated to this invoice (billing payment allocations only).",
    )
    credit_applied: CurrencyAmount = Field(
        default=Decimal("0"),
        ge=0,
        description="Sum of credit-note applications applied to this invoice.",
    )
    balance: CurrencyAmount = Field(
        ...,
        description="Outstanding balance after credit and cash: max(0, total - credit_applied - paid).",
    )
    status: InvoiceStatusLiteral = Field(description="Invoice lifecycle: DRAFT or SENT.")
    invoice_status: InvoiceStatusLiteral = Field(description="Invoice lifecycle: DRAFT, SENT.")
    payment_status: PaymentStatusLiteral = Field(
        description="Stored payment/outcome: UNPAID, PARTIALLY_PAID, PAID, OVERDUE, VOID, WRITTEN_OFF. "
        "(REFUNDED/DISPUTED are list-only filters, not persisted here.)"
    )
    refunded_amount: CurrencyAmount = Field(default=Decimal("0"), ge=0, description="Sum of completed refunds linked to this invoice.")
    has_pending_refunds: bool = Field(default=False, description="True if a refund is in INITIATED or PROCESSING for this invoice.")
    has_open_dispute: bool = Field(
        default=False,
        description="True if an allocated payment has a Braintree status indicating dispute (substring match).",
    )


class InvoiceEventEntry(BaseSchema):
    """Single invoice activity event."""

    event_type: str
    reason: str | None = None
    actor_id: str | None = None
    actor_role: str | None = None
    created_at: datetime
    display_title: str | None = Field(
        default=None,
        description="Short human-readable label for timeline UI (server-generated from event_type and reason).",
    )


class AppliedCreditNoteEntry(BaseSchema):
    """Credit note applied to this invoice."""

    credit_note_id: str
    credit_note_number: str
    applied_amount: CurrencyAmount = Field(..., ge=0)
    applied_at: date
    reason: str | None = None


class InvoiceLineItemEntry(BaseSchema):
    """Invoice line item row for detail/preview table."""

    description: str
    quantity: int = Field(..., ge=0)
    unit_price: CurrencyAmount = Field(..., ge=0)
    total_price: CurrencyAmount = Field(..., ge=0)
    line_type: str


class InvoicePaymentHistoryItem(BaseSchema):
    """Payment transaction row allocated to this invoice."""

    payment_id: str
    payment_number: str
    payment_date: date
    method: str
    transaction_id: str | None = None
    allocated_amount: CurrencyAmount = Field(..., ge=0)
    status: str


class InvoiceSummaryResponse(BaseSchema):
    """KPI summary for invoices list dashboard cards."""

    total_invoices: int = Field(..., ge=0)
    total_paid: int = Field(..., ge=0)
    total_unpaid: int = Field(..., ge=0)
    overdue: int = Field(..., ge=0)
    with_completed_refunds: int = Field(
        default=0,
        ge=0,
        description="Count of invoices in the filtered set with at least one COMPLETED refund (processed_amount > 0).",
    )
    with_open_disputes: int = Field(
        default=0,
        ge=0,
        description="Count of invoices in the filtered set with a disputed allocated payment (Braintree status).",
    )


class InvoiceRefundSummary(BaseSchema):
    """Refund rollup for invoice detail / portal."""

    refunded_amount: CurrencyAmount = Field(..., ge=0)
    pending_refund_count: int = Field(..., ge=0)
    completed_refund_count: int = Field(..., ge=0)


class InvoiceDetailResponse(BaseSchema):
    """Invoice detail with KPIs, activity, and applied credits."""

    id: str
    invoice_number: str = Field(..., min_length=1, description="Human-readable invoice code (typically INV-NNNNNN).")
    order_id: str | None = None
    order_reference: str | None = None
    organization_id: str | None = None
    customer_id: str | None = None
    issue_date: date
    due_date: date
    subtotal: CurrencyAmount = Field(..., ge=0)
    vat_rate: CurrencyAmount = Field(..., ge=0, le=100)
    vat_amount: CurrencyAmount = Field(..., ge=0)
    total: CurrencyAmount = Field(..., ge=0)
    total_after_credit: CurrencyAmount | None = None
    paid_amount: CurrencyAmount = Field(..., ge=0)
    outstanding_balance: CurrencyAmount | None = None
    status: InvoiceStatusLiteral = Field(description="Invoice lifecycle: DRAFT or SENT.")
    invoice_status: InvoiceStatusLiteral = Field(description="Invoice lifecycle: DRAFT, SENT.")
    payment_status: PaymentStatusLiteral = Field(
        description="Stored payment/outcome: UNPAID, PARTIALLY_PAID, PAID, OVERDUE, VOID, WRITTEN_OFF."
    )
    notes: str | None = Field(default=None, max_length=2000)
    billing_contact_email: str | None = Field(default=None, max_length=255)
    created_at: datetime
    updated_at: datetime
    version: int
    # KPIs
    amount_paid: CurrencyAmount | None = None
    payment_method: str | None = None
    # Activity and credits
    events: list[InvoiceEventEntry] = Field(default_factory=list)
    applied_credit_notes: list[AppliedCreditNoteEntry] = Field(default_factory=list)
    line_items: list[InvoiceLineItemEntry] = Field(default_factory=list)
    refund_summary: InvoiceRefundSummary = Field(
        default_factory=lambda: InvoiceRefundSummary(
            refunded_amount=Decimal("0"),
            pending_refund_count=0,
            completed_refund_count=0,
        )
    )
    has_open_dispute: bool = Field(default=False)


class InvoiceVoidRequest(BaseSchema):
    """Request body for void. Reason required."""

    reason: str = Field(..., min_length=1, max_length=2000, description="Reason for voiding (required).")

    @model_validator(mode="after")
    def reason_not_blank(self) -> InvoiceVoidRequest:
        if not self.reason.strip():
            raise ValueError("reason must not be blank")
        return self


class InvoiceWriteOffRequest(BaseSchema):
    """Request body for write-off. Reason required."""

    reason: str = Field(..., min_length=1, max_length=2000, description="Reason for write-off (required).")

    @model_validator(mode="after")
    def reason_not_blank(self) -> InvoiceWriteOffRequest:
        if not self.reason.strip():
            raise ValueError("reason must not be blank")
        return self


class InvoiceInternalNoteResponse(BaseSchema):
    """Single admin internal note stored on invoices.notes."""

    invoice_id: str
    notes: str | None = Field(default=None, max_length=2000, description="Null when no note is set.")
    has_note: bool = Field(description="True when notes contains non-whitespace text.")
    invoice_status: InvoiceStatusLiteral = Field(description="Invoice lifecycle: DRAFT or SENT.")
    updated_at: datetime
    version: int = Field(description="Invoice row version — pass on writes for optimistic locking.")


class InvoiceInternalNoteWriteRequest(BaseSchema):
    """Create or update the single internal note."""

    notes: str = Field(..., min_length=1, max_length=2000)
    version: int = Field(..., ge=1, description="Invoice version from last read (optimistic lock).")

    @model_validator(mode="after")
    def notes_not_blank(self) -> InvoiceInternalNoteWriteRequest:
        if not self.notes.strip():
            raise ValueError("notes must not be blank")
        return self


class InvoiceListParams(PaginationParams):
    """Query params for list: search, filters, period."""

    search: str | None = Field(default=None, max_length=100, description="Search by invoice number or order ID.")
    status: InvoiceStatusLiteral | None = Field(default=None, description="Filter by invoice (lifecycle) status: DRAFT, SENT.")
    payment_status: PaymentStatusLiteral | None = Field(
        default=None,
        description="Filter by payment status. Standard: UNPAID, PARTIALLY_PAID, PAID, OVERDUE, VOID, WRITTEN_OFF. "
        "Portal extensions: REFUNDED (completed refunds), DISPUTED (allocated payment in dispute).",
    )
    show_draft: bool = Field(default=False, description="Include draft invoices when true.")
    invoiced_from: date | None = Field(default=None, description="Invoiced date range start.")
    invoiced_to: date | None = Field(default=None, description="Invoiced date range end.")
    due_from: date | None = Field(default=None, description="Due date range start.")
    due_to: date | None = Field(default=None, description="Due date range end.")
    period: Literal["last_7_days", "last_30_days"] | None = Field(default=None, description="Shortcut for invoiced date range.")

    @model_validator(mode="after")
    def date_ranges_valid(self) -> InvoiceListParams:
        if self.invoiced_from is not None and self.invoiced_to is not None and self.invoiced_from > self.invoiced_to:
            raise ValueError("invoiced_from must be on or before invoiced_to")
        if self.due_from is not None and self.due_to is not None and self.due_from > self.due_to:
            raise ValueError("due_from must be on or before due_to")
        return self


class PdfStatusResponse(BaseSchema):
    """PDF generation status for polling."""

    status: Literal["NOT_REQUESTED", "GENERATING", "READY", "FAILED"]
    job_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    artifact_id: str | None = None


class SignedUrlRequest(BaseSchema):
    """Request body for signed URL: inline (view) or attachment (download)."""

    disposition: Literal["inline", "attachment"] = Field(default="attachment", description="inline = view in browser, attachment = download.")


class SignedUrlResponse(BaseSchema):
    """Short-lived signed URL for PDF view/download."""

    url: str
    expires_at: str = Field(..., description="ISO datetime when the URL expires.")
