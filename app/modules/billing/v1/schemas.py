from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from app.common.schemas import BaseSchema, CurrencyAmount
from app.modules.billing.enums import AllocationStatus, PaymentProvider, PaymentRecordStatus

PaymentStatusLiteral = Literal["DEPOSITED", "NOT_DEPOSITED", "PENDING", "WITHHELD_RETURNED", "VOIDED"]
AllocationStatusLiteral = Literal["ALLOCATED", "PARTIALLY_ALLOCATED", "UNALLOCATED"]
ProviderLiteral = Literal["BRAINTREE", "MANUAL", "BANK_TRANSFER", "CHEQUE", "OTHER"]
RecordPaymentClientTypeLiteral = Literal["CUSTOMER_B2B", "CUSTOMER_B2C", "B2B", "B2C"]
RefundMethodLiteral = Literal["CARD_REFUND", "BANK_TRANSFER", "CREDIT_NOTE"]
RefundTypeLiteral = Literal["FULL", "PARTIAL"]
RefundStatusLiteral = Literal["INITIATED", "PROCESSING", "COMPLETED", "FAILED", "REVERSED"]
RefundReasonCategoryLiteral = Literal[
    "BOOKING_CANCELLED",
    "SERVICE_FAILURE",
    "DUPLICATE_PAYMENT",
    "BILLING_ERROR",
    "CLIENT_REQUEST",
    "VOIDED_INVOICE",
    "OTHER",
]


class BillingPaymentCreateRequest(BaseSchema):
    client_type: RecordPaymentClientTypeLiteral
    customer_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Deprecated for B2B/admin org-scoped record-payment flow; ignored when client_type is CUSTOMER_B2B. "
            "Not accepted for CUSTOMER_B2C in this endpoint."
        ),
    )
    amount: CurrencyAmount = Field(..., gt=0)
    payment_date: date
    status: PaymentStatusLiteral = "NOT_DEPOSITED"
    provider: ProviderLiteral = "MANUAL"
    provider_txn_id: str | None = Field(default=None, max_length=255)
    transaction_fee: CurrencyAmount = Field(default=Decimal("0"), ge=0)
    braintree_status: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=500)


class BillingPaymentNotesPatchRequest(BaseSchema):
    notes: str = Field(..., max_length=500)
    version: int | None = Field(default=None, ge=1, description="Expected row version for optimistic locking (If-Match style).")


class BillingPaymentVoidRequest(BaseSchema):
    reason: str | None = Field(default=None, max_length=500)
    version: int | None = Field(default=None, ge=1, description="Expected row version for optimistic locking (If-Match style).")


class BillingPaymentInvoiceCandidateItem(BaseSchema):
    invoice_id: str
    invoice_number: str
    issue_date: date
    due_date: date
    payment_status: str
    balance_due: CurrencyAmount


class BillingPaymentAllocationUpsertRequest(BaseSchema):
    invoice_id: str
    allocated_amount: CurrencyAmount = Field(..., ge=0)
    notes: str | None = Field(default=None, max_length=2000)


class BillingPaymentAllocationBulkRequest(BaseSchema):
    allocations: list[BillingPaymentAllocationUpsertRequest] = Field(..., min_length=1, max_length=100)


class BillingPaymentAllocationReplaceItem(BaseSchema):
    invoice_id: str
    allocated_amount: CurrencyAmount = Field(..., gt=0)
    notes: str | None = Field(default=None, max_length=2000)


class BillingPaymentAllocationReplaceRequest(BaseSchema):
    allocations: list[BillingPaymentAllocationReplaceItem] = Field(default_factory=list, max_length=100)


class BillingPaymentAllocationItem(BaseSchema):
    invoice_id: str
    revision_no: int
    allocated_amount: CurrencyAmount
    notes: str | None = None
    created_at: datetime
    invoice_number: str | None = Field(default=None, description="Invoice number for this allocation row.")
    invoice_total_amount: CurrencyAmount = Field(
        ...,
        description="Invoice grand total (VAT-inclusive) at time of response.",
    )
    invoice_remaining_amount: CurrencyAmount = Field(
        ...,
        description="Outstanding invoice balance after credits and all payment allocations (same basis as allocation candidates).",
    )
    invoice_issue_date: date | None = Field(
        default=None,
        description="Invoice issue date for the allocated invoice.",
    )


class BillingPaymentAllocationSummaryItem(BaseSchema):
    invoice_id: str
    invoice_number: str | None = None
    allocated_amount: CurrencyAmount


class BillingPaymentListItem(BaseSchema):
    id: str
    organization_id: str
    client_id: str | None = Field(
        default=None,
        description=(
            "Organisation client reference for admin UI (e.g. SWC-ORG-*). "
            "Same value as ``organization_reference``; identifies the billed client company."
        ),
    )
    organization_reference: str | None = Field(
        default=None,
        description="Organisation client reference (e.g. SWC-ORG-*); useful when listing payments across all clients.",
    )
    organization_trading_name: str | None = Field(
        default=None,
        description="Trading name of the billed organisation.",
    )
    payment_number: str
    amount: CurrencyAmount
    status: PaymentStatusLiteral
    allocation_status: AllocationStatusLiteral
    allocated_amount: CurrencyAmount
    unallocated_amount: CurrencyAmount
    payment_date: date
    provider: ProviderLiteral
    provider_txn_id: str | None = None
    transaction_fee: CurrencyAmount = Decimal("0")
    dispute_amount: CurrencyAmount = Decimal("0")
    dispute_fee: CurrencyAmount = Decimal("0")
    dispute_status: str | None = None
    braintree_status: str | None = None
    braintree_status_updated_at: datetime | None = None
    remittance_advice: RemittanceAdviceSummary | None = None
    allocations: list[BillingPaymentAllocationSummaryItem] = Field(default_factory=list)
    qb_sync_status: str
    qb_last_sync_at: datetime | None = None
    created_at: datetime


class BillingPaymentKpisResponse(BaseSchema):
    total_received: CurrencyAmount
    allocated: CurrencyAmount
    unallocated: CurrencyAmount
    pending: CurrencyAmount


class PaymentHistoryListQuery(BaseSchema):
    """Query-string filters for GET /billing/payments/history and /billing/payments/kpis."""

    status: list[PaymentRecordStatus] | None = Field(
        default=None,
        description=(
            "Optional status filter. Repeat the query param for multiple values "
            "(e.g. `?status=PENDING&status=DEPOSITED`)."
        ),
    )
    allocation_status: list[AllocationStatus] | None = Field(
        default=None,
        description="Optional allocation filter. Repeat for multiple values.",
    )
    provider: list[PaymentProvider] | None = Field(
        default=None,
        description="Optional provider filter. Repeat for multiple values.",
    )
    payment_date_from: date | None = None
    payment_date_to: date | None = None
    search: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_filters(self) -> "PaymentHistoryListQuery":
        if self.status is not None and len(self.status) == 0:
            raise ValueError("`status` must include at least one value")
        if self.allocation_status is not None and len(self.allocation_status) == 0:
            raise ValueError("`allocation_status` must include at least one value")
        if self.provider is not None and len(self.provider) == 0:
            raise ValueError("`provider` must include at least one value")
        if (
            self.payment_date_from is not None
            and self.payment_date_to is not None
            and self.payment_date_from > self.payment_date_to
        ):
            raise ValueError("payment_date_from must be on or before payment_date_to")
        return self


class PaymentFilterOptionsResponse(BaseSchema):
    statuses: list[PaymentStatusLiteral]
    allocation_statuses: list[AllocationStatusLiteral]
    providers: list[ProviderLiteral]


class RemittanceAdviceSummary(BaseSchema):
    """Metadata for remittance advice stored in R2 (no object key exposed)."""

    content_type: str
    original_filename: str
    size_bytes: int
    uploaded_at: datetime


class BillingRemittanceSignedUrlResponse(BaseSchema):
    """Short-lived URL to view (inline) or download (attachment) remittance advice from R2."""

    url: str
    expires_at: str = Field(..., description="ISO 8601 datetime when the URL expires (UTC).")
    content_type: str
    disposition: str


class BillingPaymentDetailResponse(BaseSchema):
    id: str
    payment_number: str
    organization_id: str
    customer_id: str | None
    recorded_by_id: str | None
    amount: CurrencyAmount
    status: PaymentStatusLiteral
    allocation_status: AllocationStatusLiteral
    allocated_amount: CurrencyAmount
    unallocated_amount: CurrencyAmount
    payment_date: date
    provider: ProviderLiteral
    provider_txn_id: str | None = None
    transaction_fee: CurrencyAmount = Decimal("0")
    dispute_amount: CurrencyAmount = Decimal("0")
    dispute_fee: CurrencyAmount = Decimal("0")
    dispute_status: str | None = None
    braintree_status: str | None = None
    braintree_status_updated_at: datetime | None = None
    notes: str | None = None
    remittance_advice: RemittanceAdviceSummary | None = None
    qb_sync_status: str
    qb_last_sync_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    version: int
    allocations: list[BillingPaymentAllocationItem]


def _remittance_summary_from_payment(payment) -> RemittanceAdviceSummary | None:
    if not payment.remittance_advice_r2_key:
        return None
    ct = payment.remittance_advice_content_type
    fn = payment.remittance_advice_original_filename
    sz = payment.remittance_advice_size_bytes
    at = payment.remittance_advice_uploaded_at
    if not ct or not fn or sz is None or at is None:
        return None
    return RemittanceAdviceSummary(
        content_type=ct,
        original_filename=fn,
        size_bytes=int(sz),
        uploaded_at=at,
    )


def payment_to_list_item(payment) -> BillingPaymentListItem:
    allocations = []
    for row in getattr(payment, "_allocations", []) or []:
        allocations.append(
            BillingPaymentAllocationSummaryItem(
                invoice_id=str(row["invoice_id"]),
                invoice_number=row.get("invoice_number"),
                allocated_amount=Decimal(row.get("allocated_amount") or 0),
            )
        )
    org_ref = getattr(payment, "_organization_reference", None)
    return BillingPaymentListItem(
        id=payment.id,
        organization_id=str(payment.organization_id),
        client_id=org_ref,
        organization_reference=org_ref,
        organization_trading_name=getattr(payment, "_organization_trading_name", None),
        payment_number=payment.payment_number,
        amount=Decimal(payment.amount),
        status=payment.status,
        allocation_status=payment.allocation_status,
        allocated_amount=Decimal(payment.allocated_amount),
        unallocated_amount=Decimal(payment.unallocated_amount),
        payment_date=payment.payment_date,
        provider=payment.provider,
        provider_txn_id=payment.provider_txn_id,
        transaction_fee=Decimal(getattr(payment, "transaction_fee", 0) or 0),
        dispute_amount=Decimal(getattr(payment, "dispute_amount", 0) or 0),
        dispute_fee=Decimal(getattr(payment, "dispute_fee", 0) or 0),
        dispute_status=getattr(payment, "dispute_status", None),
        braintree_status=getattr(payment, "braintree_status", None),
        braintree_status_updated_at=getattr(payment, "braintree_status_updated_at", None),
        remittance_advice=_remittance_summary_from_payment(payment),
        allocations=allocations,
        qb_sync_status=payment.qb_sync_status,
        qb_last_sync_at=payment.qb_last_sync_at,
        created_at=payment.created_at,
    )


def payment_to_detail(payment, allocations: list) -> BillingPaymentDetailResponse:
    return BillingPaymentDetailResponse(
        id=payment.id,
        payment_number=payment.payment_number,
        organization_id=payment.organization_id,
        customer_id=payment.customer_id,
        recorded_by_id=payment.recorded_by_id,
        amount=Decimal(payment.amount),
        status=payment.status,
        allocation_status=payment.allocation_status,
        allocated_amount=Decimal(payment.allocated_amount),
        unallocated_amount=Decimal(payment.unallocated_amount),
        payment_date=payment.payment_date,
        provider=payment.provider,
        provider_txn_id=payment.provider_txn_id,
        transaction_fee=Decimal(getattr(payment, "transaction_fee", 0) or 0),
        dispute_amount=Decimal(getattr(payment, "dispute_amount", 0) or 0),
        dispute_fee=Decimal(getattr(payment, "dispute_fee", 0) or 0),
        dispute_status=getattr(payment, "dispute_status", None),
        braintree_status=getattr(payment, "braintree_status", None),
        braintree_status_updated_at=getattr(payment, "braintree_status_updated_at", None),
        notes=payment.notes,
        remittance_advice=_remittance_summary_from_payment(payment),
        qb_sync_status=payment.qb_sync_status,
        qb_last_sync_at=payment.qb_last_sync_at,
        created_at=payment.created_at,
        updated_at=payment.updated_at,
        version=payment.version,
        allocations=[
            BillingPaymentAllocationItem(
                invoice_id=a.invoice_id,
                revision_no=a.revision_no,
                allocated_amount=Decimal(a.allocated_amount),
                notes=a.notes,
                created_at=a.created_at,
                invoice_number=getattr(a, "_detail_invoice_number", None),
                invoice_total_amount=Decimal(str(getattr(a, "_detail_invoice_total", 0) or 0)),
                invoice_remaining_amount=Decimal(str(getattr(a, "_detail_invoice_remaining", 0) or 0)),
                invoice_issue_date=getattr(a, "_detail_invoice_issue_date", None),
            )
            for a in allocations
        ],
    )


class RefundCreateRequest(BaseSchema):
    billing_payment_id: str
    invoice_id: str | None = None
    linked_booking_ref: str | None = Field(default=None, max_length=50)
    refund_type: RefundTypeLiteral
    refund_method: RefundMethodLiteral
    reason_category: RefundReasonCategoryLiteral
    reason_description: str = Field(..., min_length=3, max_length=2000)
    amount: CurrencyAmount = Field(..., gt=0)
    metadata_json: dict | None = None


class RefundActionRequest(BaseSchema):
    note: str | None = Field(default=None, max_length=1000)
    braintree_status: str | None = Field(default=None, max_length=50)
    failure_code: str | None = Field(default=None, max_length=50)
    failure_message: str | None = Field(default=None, max_length=500)


class RefundListItem(BaseSchema):
    id: str
    refund_number: str
    payment_id: str
    payment_number: str | None = None
    invoice_id: str | None = None
    invoice_number: str | None = None
    linked_booking_ref: str | None = None
    refund_date: datetime
    amount: CurrencyAmount
    refund_type: RefundTypeLiteral
    refund_method: RefundMethodLiteral
    status: RefundStatusLiteral
    reason_category: RefundReasonCategoryLiteral
    braintree_transaction_id: str | None = None
    braintree_status: str | None = None
    processed_by_id: str | None = None
    completed_at: datetime | None = None


class RefundDetailResponse(BaseSchema):
    id: str
    refund_number: str
    organization_id: str
    billing_payment_id: str
    invoice_id: str | None = None
    linked_booking_ref: str | None = None
    provider: ProviderLiteral
    refund_method: RefundMethodLiteral
    refund_type: RefundTypeLiteral
    status: RefundStatusLiteral
    reason_category: RefundReasonCategoryLiteral
    reason_description: str
    requested_amount: CurrencyAmount
    processed_amount: CurrencyAmount
    currency: str
    braintree_transaction_id: str | None = None
    braintree_status: str | None = None
    braintree_status_updated_at: datetime | None = None
    retry_count: int
    failure_code: str | None = None
    failure_message: str | None = None
    initiated_by_id: str | None = None
    processed_by_id: str | None = None
    initiated_at: datetime | None = None
    completed_at: datetime | None = None
    metadata_json: dict | None = None
    created_at: datetime
    updated_at: datetime


class RefundEventItem(BaseSchema):
    id: str
    event_type: str
    actor_id: str | None = None
    payload_json: dict | None = None
    created_at: datetime


class RefundDetailWithEventsResponse(BaseSchema):
    refund: RefundDetailResponse
    events: list[RefundEventItem] = Field(default_factory=list)


class RefundKpisResponse(BaseSchema):
    total_refund_amount: CurrencyAmount
    refunds_this_month: int
    pending_refunds: int
    failed_refunds: int
    avg_refund_time_days: int


class RefundFilterOptionsResponse(BaseSchema):
    statuses: list[RefundStatusLiteral]
    refund_types: list[RefundTypeLiteral]
    refund_methods: list[RefundMethodLiteral]
    reason_categories: list[RefundReasonCategoryLiteral]


class RefundSourcePaymentItem(BaseSchema):
    payment_id: str
    payment_number: str
    amount: CurrencyAmount
    transaction_fee: CurrencyAmount
    provider: ProviderLiteral
    provider_txn_id: str | None = None
    braintree_status: str | None = None


CreditNotePortalStatusLiteral = Literal["OPEN", "PARTIALLY_APPLIED", "FULLY_APPLIED", "VOID"]


class CreditNoteListItem(BaseSchema):
    id: str
    credit_note_number: str
    issue_date: date
    total_credit_amount: CurrencyAmount
    applied_amount: CurrencyAmount
    remaining_amount: CurrencyAmount
    status: CreditNotePortalStatusLiteral
    reason_category: str
    reason: str | None = None
    source_invoice_id: str | None = None
    source_invoice_number: str | None = None


class CreditNoteApplicationItem(BaseSchema):
    invoice_id: str
    invoice_number: str | None = None
    applied_amount: CurrencyAmount
    applied_at: date


class CreditNoteVoidRequest(BaseSchema):
    reason: str = Field(..., min_length=1, max_length=2000)


class CreditNoteDetailResponse(BaseSchema):
    id: str
    credit_note_number: str
    organization_id: str | None = None
    customer_id: str | None = None
    source_invoice_id: str | None = None
    source_invoice_number: str | None = None
    reversal_invoice_id: str | None = None
    reversal_invoice_number: str | None = None
    issue_date: date
    total_credit_amount: CurrencyAmount
    applied_amount: CurrencyAmount
    remaining_amount: CurrencyAmount
    status: CreditNotePortalStatusLiteral
    reason_category: str
    reason: str | None = None
    currency: str
    sent_to_email: str | None = None
    sent_at: datetime | None = None
    qb_sync_status: str | None = None
    applications: list[CreditNoteApplicationItem] = Field(default_factory=list)


class CreditNoteInvoiceCandidateItem(BaseSchema):
    invoice_id: str
    invoice_number: str
    issue_date: date
    due_date: date
    payment_status: str
    outstanding_amount: CurrencyAmount


class CreditNoteApplyRequest(BaseSchema):
    invoice_id: str


class CreditNoteApplyResponse(BaseSchema):
    credit_note_id: str
    invoice_id: str
    applied_amount: CurrencyAmount
    applied_at: date


class CreditNoteCreateRequest(BaseSchema):
    organization_id: str
    source_invoice_id: str | None = None
    customer_id: str | None = None
    issue_date: date
    amount: CurrencyAmount = Field(..., gt=0)
    reason_category: str = Field(default="OTHER", max_length=40)
    reason: str | None = Field(default=None, max_length=2000)


class CreditNoteSendRequest(BaseSchema):
    email: str | None = Field(default=None, max_length=255)


class CreditNoteClientEmailResponse(BaseSchema):
    email: str | None = None


class CreditNotePdfStatusResponse(BaseSchema):
    status: str
    job_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    artifact_id: str | None = None


class CreditNotePdfSignedUrlRequest(BaseSchema):
    disposition: Literal["inline", "attachment"] = "attachment"


class CreditNotePdfSignedUrlResponse(BaseSchema):
    url: str
    expires_at: str
    disposition: str


def credit_note_to_list_item(cn) -> CreditNoteListItem:
    applied = Decimal(getattr(cn, "_applied_total", 0) or 0)
    total = Decimal(cn.total_credit_amount)
    remaining = total - applied
    status = "VOID" if cn.status in {"VOIDED", "WRITTEN_OFF"} else "OPEN"
    if status != "VOID":
        if applied > 0 and remaining <= 0:
            status = "FULLY_APPLIED"
        elif applied > 0:
            status = "PARTIALLY_APPLIED"
    src = getattr(cn, "__dict__", {}).get("source_invoice")
    return CreditNoteListItem(
        id=cn.id,
        credit_note_number=cn.credit_note_number,
        issue_date=cn.issue_date,
        total_credit_amount=total,
        applied_amount=applied,
        remaining_amount=remaining,
        status=status,
        reason_category=getattr(cn, "reason_category", "OTHER"),
        reason=cn.reason,
        source_invoice_id=cn.source_invoice_id,
        source_invoice_number=getattr(src, "invoice_number", None) if src else None,
    )


def credit_note_to_detail(cn, applied_total: Decimal, applications: list) -> CreditNoteDetailResponse:
    total = Decimal(cn.total_credit_amount)
    remaining = total - Decimal(applied_total or 0)
    status = "VOID" if cn.status in {"VOIDED", "WRITTEN_OFF"} else "OPEN"
    if status != "VOID":
        if applied_total > 0 and remaining <= 0:
            status = "FULLY_APPLIED"
        elif applied_total > 0:
            status = "PARTIALLY_APPLIED"
    src = getattr(cn, "__dict__", {}).get("source_invoice")
    rev = getattr(cn, "__dict__", {}).get("reversal_invoice")
    return CreditNoteDetailResponse(
        id=cn.id,
        credit_note_number=cn.credit_note_number,
        organization_id=cn.organization_id,
        customer_id=cn.customer_id,
        source_invoice_id=cn.source_invoice_id,
        source_invoice_number=getattr(src, "invoice_number", None) if src else None,
        reversal_invoice_id=getattr(cn, "reversal_invoice_id", None),
        reversal_invoice_number=getattr(rev, "invoice_number", None) if rev else None,
        issue_date=cn.issue_date,
        total_credit_amount=total,
        applied_amount=Decimal(applied_total or 0),
        remaining_amount=remaining,
        status=status,
        reason_category=getattr(cn, "reason_category", "OTHER"),
        reason=cn.reason,
        currency=cn.currency,
        sent_to_email=getattr(cn, "sent_to_email", None),
        sent_at=getattr(cn, "sent_at", None),
        qb_sync_status=getattr(cn, "qb_sync_status", None),
        applications=[
            CreditNoteApplicationItem(
                invoice_id=a.invoice_id,
                invoice_number=getattr(getattr(a, "invoice", None), "invoice_number", None),
                applied_amount=Decimal(a.applied_amount),
                applied_at=a.applied_at,
            )
            for a in applications
        ],
    )


def refund_to_list_item(refund) -> RefundListItem:
    return RefundListItem(
        id=refund.id,
        refund_number=refund.refund_number,
        payment_id=refund.billing_payment_id,
        payment_number=getattr(refund, "_payment_number", None),
        invoice_id=refund.invoice_id,
        invoice_number=getattr(refund, "_invoice_number", None),
        linked_booking_ref=refund.linked_booking_ref,
        refund_date=refund.created_at,
        amount=Decimal(refund.processed_amount or refund.requested_amount),
        refund_type=refund.refund_type,
        refund_method=refund.refund_method,
        status=refund.status,
        reason_category=refund.reason_category,
        braintree_transaction_id=refund.braintree_transaction_id,
        braintree_status=refund.braintree_status,
        processed_by_id=refund.processed_by_id,
        completed_at=refund.completed_at,
    )


def refund_to_detail(refund) -> RefundDetailResponse:
    return RefundDetailResponse(
        id=refund.id,
        refund_number=refund.refund_number,
        organization_id=refund.organization_id,
        billing_payment_id=refund.billing_payment_id,
        invoice_id=refund.invoice_id,
        linked_booking_ref=refund.linked_booking_ref,
        provider=refund.provider,
        refund_method=refund.refund_method,
        refund_type=refund.refund_type,
        status=refund.status,
        reason_category=refund.reason_category,
        reason_description=refund.reason_description,
        requested_amount=Decimal(refund.requested_amount),
        processed_amount=Decimal(refund.processed_amount),
        currency=refund.currency,
        braintree_transaction_id=refund.braintree_transaction_id,
        braintree_status=refund.braintree_status,
        braintree_status_updated_at=refund.braintree_status_updated_at,
        retry_count=refund.retry_count,
        failure_code=refund.failure_code,
        failure_message=refund.failure_message,
        initiated_by_id=refund.initiated_by_id,
        processed_by_id=refund.processed_by_id,
        initiated_at=refund.initiated_at,
        completed_at=refund.completed_at,
        metadata_json=refund.metadata_json,
        created_at=refund.created_at,
        updated_at=refund.updated_at,
    )
