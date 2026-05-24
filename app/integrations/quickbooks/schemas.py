"""Pydantic schemas for QuickBooks integration API."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal, Self, TypeAlias

from pydantic import Field, model_validator

from app.common.schemas import BaseSchema
from app.integrations.quickbooks.log_date_range import MAX_QB_LOG_FILTER_DAYS
from app.modules.orders.enums import SummaryPeriodPreset

QuickBooksLogStatus: TypeAlias = Literal["PENDING", "SYNCED", "FAILED", "RETRYING", "SKIPPED"]
QuickBooksLogAction: TypeAlias = Literal["Queued", "Created", "Updated", "Deleted", "No Change", "Credit Applied"]
QuickBooksLogEventType: TypeAlias = Literal[
    "CUSTOMER_QUEUED",
    "CUSTOMER_CREATED",
    "CUSTOMER_UPDATED",
    "CUSTOMER_DELETED",
    "INVOICE_QUEUED",
    "INVOICE_CREATED",
    "INVOICE_UPDATED",
    "INVOICE_DELETED",
    "INVOICE_NO_CHANGE",
    "CREDIT_NOTE_QUEUED",
    "CREDIT_NOTE_CREATED",
    "CREDIT_NOTE_UPDATED",
    "CREDIT_NOTE_DELETED",
    "CREDIT_NOTE_NO_CHANGE",
    "CREDIT_APPLICATION_APPLIED",
    "PAYMENT_QUEUED",
    "PAYMENT_CREATED",
    "PAYMENT_UPDATED",
    "PAYMENT_NO_CHANGE",
]


class QuickBooksEntityType(str, Enum):
    CUSTOMER = "customer"
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    CREDIT_APPLICATION = "credit_application"
    PAYMENT = "payment"


class QuickBooksResyncEntityType(str, Enum):
    CUSTOMER = "customer"
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    PAYMENT = "payment"


class QuickBooksConnectUrlResponse(BaseSchema):
    authorization_url: str
    state: str


class QuickBooksCallbackResponse(BaseSchema):
    connected: bool
    realm_id: str


class QuickBooksStatusResponse(BaseSchema):
    connected: bool
    realm_id: str | None = None
    expires_at: datetime | None = None
    connection_status: str | None = None
    status_created_at: datetime | None = None
    last_refreshed_at: datetime | None = None
    last_synced_at: datetime | None = None
    failed_syncs: int = 0
    last_error_at: datetime | None = None
    last_error: str | None = None


class QuickBooksSyncRequest(BaseSchema):
    force: bool = Field(
        default=False,
        description=(
            "When true, forces a full QuickBooks upsert path for the requested entity "
            "(for invoices this also re-runs credit-application synchronization)."
        ),
    )


class QuickBooksSyncResult(BaseSchema):
    queued: bool
    job_id: str | None = None
    entity_type: QuickBooksResyncEntityType
    local_entity_id: str
    sync_status: str


class QuickBooksMappingUpsertRequest(BaseSchema):
    qb_ref_id: str = Field(..., min_length=1, max_length=100)
    qb_ref_name: str | None = Field(default=None, max_length=255)
    is_active: bool = Field(default=True)
    metadata: dict | None = None


class QuickBooksMappingResponse(BaseSchema):
    id: str
    mapping_type: str
    local_key: str
    qb_ref_id: str
    qb_ref_name: str | None = None
    is_active: bool
    metadata: dict | None = None
    created_at: datetime
    updated_at: datetime


class QuickBooksMappingsListResponse(BaseSchema):
    items: list[QuickBooksMappingResponse]


class QuickBooksMappingsListQuery(BaseSchema):
    mapping_type: str | None = None
    is_active: bool | None = None
    limit: int = Field(default=200, ge=1, le=500)


class QuickBooksSyncSettingsUpdateRequest(BaseSchema):
    strict_mapping_mode: bool | None = None
    sync_attachments: bool | None = None
    auto_retry_enabled: bool | None = None
    max_retry_attempts: int | None = Field(default=None, ge=0, le=20)
    retry_backoff_seconds: int | None = Field(default=None, ge=1, le=3600)
    allow_force_reapply_credit: bool | None = None


class QuickBooksSyncSettingsResponse(BaseSchema):
    strict_mapping_mode: bool
    sync_attachments: bool
    auto_retry_enabled: bool
    max_retry_attempts: int
    retry_backoff_seconds: int
    allow_force_reapply_credit: bool
    created_at: datetime
    updated_at: datetime


class QuickBooksPreflightResult(BaseSchema):
    invoice_id: str
    valid: bool
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class QuickBooksSyncHealthResponse(BaseSchema):
    failed_last_24h: int
    failed_last_7d: int
    pending_links: int
    last_failure_at: datetime | None = None


class QuickBooksReconcileResponse(BaseSchema):
    missing_invoice_links: int
    missing_credit_note_links: int
    failed_invoice_links: int
    failed_credit_note_links: int
    failed_credit_application_links: int


class QuickBooksResyncRequest(BaseSchema):
    force: bool = Field(default=False)


class QuickBooksFailuresListQuery(BaseSchema):
    status: list[QuickBooksLogStatus] | None = Field(
        default=None,
        description=(
            "Optional status filter. Repeat the query param for multiple values "
            "(e.g. `?status=FAILED&status=PENDING`). Allowed values: PENDING, SYNCED, FAILED, RETRYING, SKIPPED. "
            "Omit to return logs for all statuses."
        ),
    )
    entity_type: QuickBooksEntityType | None = None
    event_type: QuickBooksLogEventType | None = None
    action: QuickBooksLogAction | None = None
    error_code: str | None = None
    job_id: str | None = None
    local_entity_id: str | None = None
    search: str | None = None
    period: SummaryPeriodPreset | None = Field(
        default=None,
        description=(
            "Preset created_at window: TODAY, LAST_7_DAYS, LAST_WEEK (prior Mon–Sun), "
            "LAST_30_DAYS, LAST_MONTH (prior calendar month). When set, `date_from`/`date_to` must be omitted."
        ),
    )
    date_from: date | None = Field(
        default=None,
        description="Inclusive start date for created_at (calendar day, UTC). Requires `date_to` when `period` is omitted.",
    )
    date_to: date | None = Field(
        default=None,
        description=(
            "Inclusive end date for created_at (calendar day, UTC). Cannot be in the future. "
            "Requires `date_from` when `period` is omitted."
        ),
    )
    limit: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def validate_filters(self) -> Self:
        if self.status is not None and len(self.status) == 0:
            raise ValueError("`status` must include at least one value")

        has_period = self.period is not None
        has_from = self.date_from is not None
        has_to = self.date_to is not None
        if has_period and (has_from or has_to):
            raise ValueError("Provide either `period` or both `date_from` and `date_to`, not both")
        if has_from ^ has_to:
            raise ValueError("Both `date_from` and `date_to` are required for a custom date range")
        if has_from and has_to:
            assert self.date_from is not None and self.date_to is not None
            if self.date_from > self.date_to:
                raise ValueError("date_from cannot be later than date_to")
            if self.date_to > date.today():
                raise ValueError("date_to cannot be in the future")
            span_days = (self.date_to - self.date_from).days + 1
            if span_days > MAX_QB_LOG_FILTER_DAYS:
                raise ValueError(f"Date range cannot exceed {MAX_QB_LOG_FILTER_DAYS} days")
        return self


class QuickBooksFailureLogListItem(BaseSchema):
    id: str
    entity_type: QuickBooksEntityType
    event_type: QuickBooksLogEventType | None = None
    local_entity_id: str | None = None
    action: QuickBooksLogAction
    status: QuickBooksLogStatus
    attempt_no: int
    job_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    related_qb_id: str | None = None
    created_at: datetime


class QuickBooksFailureLogDetailResponse(BaseSchema):
    id: str
    entity_type: QuickBooksEntityType
    event_type: QuickBooksLogEventType | None = None
    local_entity_id: str | None = None
    action: QuickBooksLogAction
    status: QuickBooksLogStatus
    attempt_no: int
    job_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    related_qb_id: str | None = None
    payload: dict | None = None
    created_at: datetime


class QuickBooksFailureLogsListResponse(BaseSchema):
    items: list[QuickBooksFailureLogListItem]


class QuickBooksBulkResyncRequest(BaseSchema):
    status: QuickBooksLogStatus | None = Field(
        default=None,
        description=(
            "Legacy single-status filter. Allowed values: PENDING, SYNCED, FAILED, RETRYING, SKIPPED. "
            "Use either `status` or `statuses`, not both."
        ),
    )
    statuses: list[QuickBooksLogStatus] | None = Field(
        default=None,
        description=(
            "Optional multi-status filter. Allowed values per item: PENDING, SYNCED, FAILED, RETRYING, SKIPPED. "
            "Defaults to ['FAILED', 'PENDING'] when not provided."
        ),
    )
    entity_type: QuickBooksEntityType | None = Field(
        default=None,
        description="Allowed values: customer, invoice, credit_note, credit_application.",
    )
    event_type: QuickBooksLogEventType | None = Field(
        default=None,
        description=(
            "Allowed values: CUSTOMER_QUEUED, CUSTOMER_CREATED, CUSTOMER_UPDATED, CUSTOMER_DELETED, "
            "INVOICE_QUEUED, INVOICE_CREATED, INVOICE_UPDATED, INVOICE_DELETED, INVOICE_NO_CHANGE, "
            "CREDIT_NOTE_QUEUED, CREDIT_NOTE_CREATED, CREDIT_NOTE_UPDATED, CREDIT_NOTE_DELETED, "
            "CREDIT_NOTE_NO_CHANGE, CREDIT_APPLICATION_APPLIED."
        ),
    )
    action: QuickBooksLogAction | None = Field(
        default=None,
        description="Allowed values: Queued, Created, Updated, Deleted, No Change, Credit Applied.",
    )
    error_code: str | None = Field(default=None, min_length=1, max_length=80)
    include_non_connection_failures: bool = Field(
        default=False,
        description=(
            "When false (default), only retryable transient FAILED logs are replayed "
            "(for example connection/auth, timeout, rate-limit, temporary upstream errors); "
            "PENDING logs remain eligible."
        ),
    )
    force: bool = Field(default=False)
    batch_size: int = Field(default=200, ge=10, le=500)
    limit: int = Field(default=2000, ge=1, le=10000)

    @model_validator(mode="after")
    def validate_status_filters(self) -> "QuickBooksBulkResyncRequest":
        if self.status is not None and self.statuses is not None:
            raise ValueError("Use either `status` or `statuses`, not both")
        if self.statuses is not None and len(self.statuses) == 0:
            raise ValueError("`statuses` must include at least one status")
        return self


class QuickBooksFinalFailuresResyncRequest(BaseSchema):
    entity_type: QuickBooksEntityType | None = Field(
        default=None,
        description="Allowed values: customer, invoice, credit_note, credit_application.",
    )
    event_type: QuickBooksLogEventType | None = Field(
        default=None,
        description=(
            "Allowed values: CUSTOMER_QUEUED, CUSTOMER_CREATED, CUSTOMER_UPDATED, CUSTOMER_DELETED, "
            "INVOICE_QUEUED, INVOICE_CREATED, INVOICE_UPDATED, INVOICE_DELETED, INVOICE_NO_CHANGE, "
            "CREDIT_NOTE_QUEUED, CREDIT_NOTE_CREATED, CREDIT_NOTE_UPDATED, CREDIT_NOTE_DELETED, "
            "CREDIT_NOTE_NO_CHANGE, CREDIT_APPLICATION_APPLIED."
        ),
    )
    action: QuickBooksLogAction | None = Field(
        default=None,
        description="Allowed values: Queued, Created, Updated, Deleted, No Change, Credit Applied.",
    )
    error_code: str | None = Field(default=None, min_length=1, max_length=80)
    force: bool = Field(default=False)
    batch_size: int = Field(default=200, ge=10, le=500)
    limit: int = Field(default=2000, ge=1, le=10000)


class QuickBooksBulkResyncResponse(BaseSchema):
    requested: int
    queued: int
    skipped: int
    items: list[QuickBooksSyncResult] = Field(default_factory=list)
