"""Driver v1 API schemas — request and response models."""

from __future__ import annotations

import enum
import re
import datetime as dt
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal, Optional, Self
from uuid import UUID as UuidType

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.common.constants import MIN_PASSWORD_LENGTH
from app.common.schemas import BaseSchema, CurrencyAmount, PaginatedResponse, PaginationParams
from app.modules.drivers.enums import (
    DriverMapPreference,
    CalendarEventSource,
    DriverAccountStatus,
    DriverCapacity,
    DriverDocumentKind,
    DriverDocumentStatus,
    DriverLiveStatus,
    DriverMissingPackageReasonCode,
    DriverStopPackageFinalStatus,
    DriverType,
    ShiftStatus,
    TimeOffType,
    TrafficViolationStatus,
    TrafficViolationType,
)
from app.modules.planning.enums import RouteStatus, RouteType

# ── List / detail responses ────────────────────────────────────────────────────


class DriverUserBrief(BaseSchema):
    """Minimal user info embedded in driver detail (no PII overflow)."""

    id: str
    email: str | None = Field(
        default=None,
        description="Linked user email; null on drafts until captured in draft_data or submit.",
    )
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None


class DriverListEntry(BaseSchema):
    """Single driver in list view."""

    id: str
    user_id: str
    driver_code: str
    first_name: str = Field(..., description="Resolved from linked user profile")
    last_name: str = Field(..., description="Resolved from linked user profile")
    phone: str | None = Field(default=None, description="Resolved from linked user profile")
    capacities: list[DriverCapacity] = Field(..., description="One or more vehicle capacities")
    account_status: DriverAccountStatus
    live_status: DriverLiveStatus
    safety_score: int | None = None
    created_at: datetime
    updated_at: datetime
    version: int


class DriverDraftListEntry(BaseSchema):
    """Single driver in draft list view (includes extra identity fields)."""

    id: str
    user_id: str | None = None
    driver_code: str
    draft_id: str | None = Field(default=None, description="Stable draft code (DF-NNN) from driver_drafts pivot")
    draft_created_by: str | None = Field(default=None, description="Admin user id that created the draft pivot row")
    draft_created_at: datetime | None = None
    draft_updated_at: datetime | None = None
    is_submitted: bool = False

    email: str | None = None
    first_name: str | None = Field(default=None, description="Resolved from linked user profile")
    last_name: str | None = Field(default=None, description="Resolved from linked user profile")
    phone: str | None = Field(default=None, description="Resolved from linked user profile")

    capacities: list[DriverCapacity] = Field(..., description="One or more vehicle capacities")
    driver_type: DriverType | None = None

    country: str | None = None
    state: str | None = None
    city: str | None = None
    postcode: str | None = None

    account_status: DriverAccountStatus
    live_status: DriverLiveStatus
    safety_score: int | None = None
    created_at: datetime
    updated_at: datetime
    version: int


class DriverDetailResponse(BaseSchema):
    """Full driver with user info (detail view)."""

    id: str
    user_id: str | None = None
    driver_code: str
    user: DriverUserBrief | None = None
    depot_id: str | None = None
    vehicle_id: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    postcode: str | None = None
    country: str | None = None
    state: str | None = None
    capacities: list[DriverCapacity] = Field(..., description="One or more vehicle capacities")
    driver_type: DriverType | None = None
    license_number: str | None = None
    license_category: str | None = None
    max_stops: int = 30
    territory_tags: list[str] | None = None
    account_status: DriverAccountStatus
    live_status: DriverLiveStatus
    safety_score: int | None = None
    on_time_deliveries: int | None = None
    notes: str | None = None
    okay_with_layover: bool = Field(default=False, description="Whether the driver accepts layovers.")
    layover_cost_per_night: CurrencyAmount = Field(
        default=Decimal("0"),
        description="Layover cost per night in GBP (stored as decimal currency).",
    )
    max_layover_nights: int = Field(default=0, ge=0, le=366)
    profile_photo_url: str | None = None
    documents: DriverDocumentsListResponse | None = None
    created_at: datetime
    updated_at: datetime
    version: int


# ── Create / update requests ────────────────────────────────────────────────────


class DriverCreateRequest(BaseSchema):
    """Create driver (link to existing user)."""

    user_id: str = Field(..., description="Existing user ID to link as driver")

    # Identity
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone: str = Field(..., min_length=5, max_length=50)
    email: str = Field(..., max_length=255)

    # Capacity / type
    capacities: list[DriverCapacity] | None = Field(default=None, description="One or more vehicle capacities")
    driver_type: DriverType = Field(default=DriverType.INTERNAL)

    # Address
    address_line1: str = Field(..., max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    city: str = Field(..., max_length=100)
    postcode: str = Field(..., max_length=20)
    latitude: float | None = Field(default=None)
    longitude: float | None = Field(default=None)

    depot_id: str | None = Field(default=None, description="Assigned depot")
    vehicle_id: str | None = Field(default=None, description="Assigned vehicle")
    license_number: str | None = Field(default=None, max_length=50)
    license_category: str | None = Field(default=None, max_length=20)
    max_stops: int = Field(default=30, ge=1, le=500)
    territory_tags: list[str] | None = Field(default=None, max_length=50)
    account_status: DriverAccountStatus = Field(
        default=DriverAccountStatus.DRAFT,
        description="Lifecycle status: DRAFT, PENDING_ACTIVATION, ACTIVE, SUSPENDED, INACTIVE",
    )
    live_status: DriverLiveStatus = Field(
        default=DriverLiveStatus.OFFLINE,
        description="Live operational status: ON_ROUTE, ON_BREAK, TIME_OFF, RETURNING, OFFLINE, NON_WORKING_DAY",
    )
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("max_stops")
    @classmethod
    def validate_max_stops(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_stops must be positive")
        return v

    @model_validator(mode="after")
    def validate_capacity_inputs(self) -> Self:
        capacities = list(self.capacities or [])
        if not capacities:
            raise ValueError("capacities must be provided")
        deduped = list(dict.fromkeys(capacities))
        object.__setattr__(self, "capacities", deduped)
        return self


class DriverUpdateRequest(BaseSchema):
    """Update driver (partial)."""

    depot_id: str | None = None
    vehicle_id: str | None = None
    first_name: str | None = Field(default=None, max_length=100, description="Updates linked user profile")
    last_name: str | None = Field(default=None, max_length=100, description="Updates linked user profile")
    phone: str | None = Field(default=None, max_length=50, description="Updates linked user profile")
    email: str | None = Field(default=None, max_length=255, description="Updates linked user profile")
    capacities: list[DriverCapacity] | None = None
    driver_type: DriverType | None = None
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    postcode: str | None = Field(default=None, max_length=20)
    license_number: str | None = Field(default=None, max_length=50)
    license_category: str | None = Field(default=None, max_length=20)
    max_stops: int | None = Field(default=None, ge=1, le=500)
    territory_tags: list[str] | None = None
    account_status: DriverAccountStatus | None = None
    live_status: DriverLiveStatus | None = None
    notes: str | None = Field(default=None, max_length=2000)
    okay_with_layover: bool | None = None
    layover_cost_per_night: Decimal | None = Field(default=None, ge=0)
    max_layover_nights: int | None = Field(default=None, ge=0, le=366)
    expected_version: int | None = Field(default=None, description="For optimistic locking")

    @model_validator(mode="after")
    def normalize_capacity_updates(self) -> Self:
        capacities = list(self.capacities or [])
        if capacities:
            deduped = list(dict.fromkeys(capacities))
            object.__setattr__(self, "capacities", deduped)
        return self


class DriverOperationalConfigurationResponse(BaseSchema):
    """Layover preferences (subset of driver profile)."""

    okay_with_layover: bool
    layover_cost_per_night: CurrencyAmount
    max_layover_nights: int = Field(ge=0, le=366)


class DriverOperationalConfigurationUpdateRequest(BaseSchema):
    """Update operational configuration only (admin Edit Configurations modal)."""

    okay_with_layover: bool
    layover_cost_per_night: CurrencyAmount
    max_layover_nights: int = Field(ge=0, le=366)
    expected_version: int | None = Field(default=None, description="Driver row version for optimistic locking")


# ── Query params ────────────────────────────────────────────────────────────────


class DriverListParams(PaginationParams):
    """Query params for list drivers."""

    account_status: DriverAccountStatus | None = Field(default=None, description="Filter by account status")
    live_status: DriverLiveStatus | None = Field(default=None, description="Filter by live status")
    depot_id: str | None = Field(default=None, description="Filter by depot")
    order_by: str | None = Field(default="created_at", description="Sort field")
    order_desc: bool = Field(default=True, description="Sort descending")


class DriverKpis(BaseSchema):
    total_employed: int = Field(
        description=(
            "Drivers in the default GET /v1/drivers result set (no query filters): excludes DRAFT and "
            "profiles without a linked user. Unaffected by list search/filters — compare to "
            "data.table.total only when loading the driver list without filters."
        ),
    )
    active_now: int = Field(description="ACTIVE drivers with a linked user.")
    suspended: int = Field(description="SUSPENDED drivers with a linked user.")
    pending_activation: int = Field(description="PENDING_ACTIVATION drivers with a linked user.")


class DriverListResponse(BaseSchema):
    kpis: DriverKpis
    table: PaginatedResponse[DriverListEntry]


class DriverDraftListResponse(BaseSchema):
    """Draft-only list response (table-only, no KPIs)."""

    table: PaginatedResponse[DriverDraftListEntry]


class DriverDraftCreateRequest(BaseSchema):
    """Create a driver draft (creates both User(role=DRIVER) and Driver(account_status=DRAFT)).

    Note: user identity fields are required by the users table; driver profile fields are optional for drafts.
    """

    # User fields (required by users table)
    email: str = Field(..., max_length=255)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=50)

    # Driver draft fields (optional)
    capacities: list[DriverCapacity] | None = None
    driver_type: DriverType | None = None
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    postcode: str | None = Field(default=None, max_length=20)
    latitude: float | None = Field(default=None)
    longitude: float | None = Field(default=None)
    depot_id: str | None = None
    vehicle_id: str | None = None
    license_number: str | None = Field(default=None, max_length=50)
    license_category: str | None = Field(default=None, max_length=20)
    max_stops: int | None = Field(default=None, ge=1, le=500)
    territory_tags: list[str] | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _dedupe_capacities(self) -> Self:
        if self.capacities:
            deduped = list(dict.fromkeys(self.capacities))
            object.__setattr__(self, "capacities", deduped)
        return self


class DriverDraftUpdateRequest(BaseSchema):
    """Update an existing driver draft (partial). Must include at least one field."""

    depot_id: str | None = None
    vehicle_id: str | None = None
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    capacities: list[DriverCapacity] | None = None
    driver_type: DriverType | None = None
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    postcode: str | None = Field(default=None, max_length=20)
    license_number: str | None = Field(default=None, max_length=50)
    license_category: str | None = Field(default=None, max_length=20)
    max_stops: int | None = Field(default=None, ge=1, le=500)
    territory_tags: list[str] | None = None
    notes: str | None = Field(default=None, max_length=2000)
    okay_with_layover: bool | None = None
    layover_cost_per_night: Decimal | None = Field(default=None, ge=0)
    max_layover_nights: int | None = Field(default=None, ge=0, le=366)
    expected_version: int | None = Field(default=None, description="For optimistic locking")

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        # expected_version alone does not count as a mutation.
        values = self.model_dump()
        values.pop("expected_version", None)
        if all(v is None for v in values.values()):
            raise ValueError("At least one field must be provided to update a draft")
        return self

    @model_validator(mode="after")
    def _dedupe_capacities(self) -> Self:
        if self.capacities:
            deduped = list(dict.fromkeys(self.capacities))
            object.__setattr__(self, "capacities", deduped)
        return self


class DriverDraftUpsertResponse(BaseSchema):
    draft_id: str
    driver: DriverDetailResponse


# ── Documents ──────────────────────────────────────────────────────────────────────


class DriverDocumentCreateRequest(BaseSchema):
    """Create/upload driver document. Title is derived from document_type (enum with spaces) except for CUSTOM where a custom title is required."""

    document_type: DriverDocumentKind
    title: str | None = None
    expiry_date: date | None = None

    @model_validator(mode="after")
    def validate_title_for_type(self) -> Self:
        if self.document_type is DriverDocumentKind.CUSTOM:
            if not self.title or not str(self.title).strip():
                raise ValueError("title is required when document_type is CUSTOM")
        else:
            display = self.document_type.to_display_title()
            if self.title is not None and str(self.title).strip() != display:
                raise ValueError(f"title must be '{display}' for document_type {self.document_type.value} or omitted")
            # Set title to canonical display form when omitted
            object.__setattr__(self, "title", display)
        return self


class InitialDriverDocumentMeta(BaseSchema):
    """Metadata for a document uploaded during add-new-driver onboarding."""

    document_type: DriverDocumentKind
    title: str | None = Field(default=None, max_length=255)
    expiry_date: date | None = None

    @field_validator("expiry_date")
    @classmethod
    def expiry_not_in_past(cls, v: date | None) -> date | None:
        if v is not None and v < date.today():
            raise ValueError("expiry_date cannot be in the past")
        return v

    @model_validator(mode="after")
    def validate_title_for_type(self) -> Self:
        if self.document_type is DriverDocumentKind.CUSTOM:
            if not self.title or not str(self.title).strip():
                raise ValueError("title is required when document_type is CUSTOM")
        else:
            display = self.document_type.to_display_title()
            if self.title is not None and str(self.title).strip() != display:
                raise ValueError(f"title must be '{display}' for document_type {self.document_type.value} or omitted")
            object.__setattr__(self, "title", display)
        return self


class OnboardDrivingLicenceDocumentMeta(BaseSchema):
    """Metadata for the required driving licence file on POST /add-new-driver."""

    document_type: DriverDocumentKind = Field(
        default=DriverDocumentKind.DRIVING_LICENCE,
        description="Must be DRIVING_LICENCE; custom documents use POST /drivers/{driver_id}/documents.",
    )
    title: str | None = Field(default=None, max_length=255)
    expiry_date: date

    @field_validator("expiry_date")
    @classmethod
    def expiry_not_in_past(cls, v: date) -> date:
        if v < date.today():
            raise ValueError("expiry_date cannot be in the past")
        return v

    @field_validator("document_type")
    @classmethod
    def only_driving_licence(cls, v: DriverDocumentKind) -> DriverDocumentKind:
        if v is not DriverDocumentKind.DRIVING_LICENCE:
            raise ValueError(
                "Only DRIVING_LICENCE is allowed when creating a driver; upload custom documents via "
                "POST /v1/drivers/{driver_id}/documents."
            )
        return v

    @model_validator(mode="after")
    def canonical_title(self) -> Self:
        display = DriverDocumentKind.DRIVING_LICENCE.to_display_title()
        if self.title is not None and str(self.title).strip() != display:
            raise ValueError(f"title must be '{display}' for driving licence or omitted")
        object.__setattr__(self, "title", display)
        return self


class DriverDocumentResponse(BaseSchema):
    id: str
    driver_id: str
    document_type: DriverDocumentKind
    title: str | None = None
    file_url: str | None = Field(None, description="URL for preview/download of the stored file")
    expiry_date: date | None = None
    status: DriverDocumentStatus = Field(
        ...,
        description="Auto-calculated: VALID, EXPIRING_SOON (expires within 30 days), or EXPIRED",
    )


class DriverDocumentsListResponse(BaseSchema):
    items: list[DriverDocumentResponse]


# ── Driver document access OTP (step-up for compliance documents) ─────────────


class DriverDocOTPSendResponse(BaseSchema):
    """Returned after POST /v1/drivers/documents/otp/send."""

    message: str = "OTP sent to your registered email address. It expires in 10 minutes."


class DriverDocAccessTokenResponse(BaseSchema):
    """Returned after POST /v1/drivers/documents/otp/verify."""

    driver_doc_access_token: str = Field(
        ...,
        description=(
            "Pass this token as the `X-Driver-Doc-Access-Token` header on driver compliance document requests. "
            "Valid for 1 hour from issue time."
        ),
    )
    expires_in: int = Field(3600, description="Seconds until the token expires.")
    expires_at: datetime = Field(..., description="UTC datetime when the token expires.")
    message: str


class DriverDocumentResult(BaseSchema):
    """Result of processing an initial driver document during onboarding."""

    type: DriverDocumentKind
    status: str = Field(..., description="One of: success, failed")
    error: str | None = Field(
        default=None,
        description="Optional, user-safe error message when status == 'failed'",
    )


# ── Time off ───────────────────────────────────────────────────────────────────────


class DriverTimeOffEntry(BaseSchema):
    id: str
    driver_id: str
    start_date: date
    end_date: date
    type: TimeOffType
    days: int | None = None
    notes: str | None = None
    is_paid: bool = True


class DriverTimeOffListResponse(BaseSchema):
    items: list[DriverTimeOffEntry]
    paid_leave_taken: int
    unpaid_leave_taken: int


class SuspendDriverRequest(BaseSchema):
    reason: str | None = Field(default=None, max_length=2000)


class ReactivateDriverRequest(BaseSchema):
    """Optional reason when an admin reactivates a suspended driver."""

    reason: str | None = Field(default=None, max_length=2000)


class AdminDriverPasswordChangeRequest(BaseSchema):
    """Admin-initiated driver password change (no current password required)."""

    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)


# ── Weekly schedule ─────────────────────────────────────────────────────────


class WeeklyScheduleDay(BaseSchema):
    day_of_week: int = Field(..., ge=0, le=6, description="0 = Monday, 6 = Sunday")
    is_active: bool
    start_time: time | None = None
    end_time: time | None = None


class WeeklyScheduleResponse(BaseSchema):
    days: list[WeeklyScheduleDay]
    total_weekly_hours: float


# ── Shifts ──────────────────────────────────────────────────────────────────


class DriverShiftEntry(BaseSchema):
    id: str
    driver_id: str
    date: date
    start_time: time
    end_time: time
    status: str


class DriverShiftListResponse(BaseSchema):
    items: list[DriverShiftEntry]


# ── Work schedule (mobile calendar) ────────────────────────────────────────


class WorkScheduleDayType(str, enum.Enum):
    WORKING = "WORKING"
    TIME_OFF = "TIME_OFF"
    HOLIDAY = "HOLIDAY"
    REST = "REST"


class WorkScheduleRouteInfo(BaseSchema):
    route_id: str
    route_code: str
    route_status: str
    vehicle_registration: str | None = None


class WorkScheduleDayEntry(BaseSchema):
    date: date
    day_type: WorkScheduleDayType
    shift_hours: str | None = None
    shift_status: str | None = None
    time_off_type: str | None = None
    time_off_is_paid: bool | None = None
    holiday_name: str | None = None
    route: WorkScheduleRouteInfo | None = None


class WorkScheduleWeeklyResponse(BaseSchema):
    start_date: date
    end_date: date
    days: list[WorkScheduleDayEntry]


class WorkScheduleMonthlyResponse(BaseSchema):
    month: str
    days: list[WorkScheduleDayEntry]


class WorkScheduleDayDetailResponse(BaseSchema):
    date: date
    day_type: WorkScheduleDayType
    shift_hours: str | None = None
    shift_status: str | None = None
    time_off_type: str | None = None
    time_off_is_paid: bool | None = None
    holiday_name: str | None = None
    vehicle: str | None = None
    route: WorkScheduleRouteInfo | None = None


# ── Traffic violations ─────────────────────────────────────────────────────-


class TrafficViolationProofEntry(BaseSchema):
    id: str
    url: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    created_at: datetime


class TrafficViolationProofUploadResult(BaseSchema):
    index: int
    filename: str
    status: Literal["success", "failed"]
    error: str | None = None
    proof: TrafficViolationProofEntry | None = None


class TrafficViolationEntry(BaseSchema):
    id: str
    driver_id: str
    occurred_at: datetime
    violation_type: TrafficViolationType
    amount: Decimal
    status: TrafficViolationStatus
    notes: str | None = None
    proofs: list[TrafficViolationProofEntry] = Field(default_factory=list)


class TrafficViolationListResponse(BaseSchema):
    items: list[TrafficViolationEntry]
    total: int
    page: int
    size: int


class TrafficViolationUpsertResponse(BaseSchema):
    """Traffic violation payload plus per-proof upload results (when files were supplied)."""

    violation: TrafficViolationEntry
    proof_results: list[TrafficViolationProofUploadResult] = Field(default_factory=list)


class DriverWithUserCreateRequest(BaseSchema):
    """Create both User (role=DRIVER) and Driver in a single call."""

    # User fields
    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=128)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=50)

    # Driver profile fields (same as DriverCreateRequest minus user_id/email/name)
    capacities: list[DriverCapacity] | None = Field(default=None, description="One or more vehicle capacities")
    driver_type: DriverType = Field(default=DriverType.INTERNAL)
    address_line1: str = Field(..., max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    city: str = Field(..., max_length=100)
    postcode: str = Field(..., max_length=20)
    latitude: float | None = Field(default=None)
    longitude: float | None = Field(default=None)
    depot_id: str | None = Field(default=None, description="Assigned depot")
    vehicle_id: str | None = Field(default=None, description="Assigned vehicle")
    license_number: str | None = Field(default=None, max_length=50)
    license_category: str | None = Field(default=None, max_length=20)
    max_stops: int = Field(default=30, ge=1, le=500)
    territory_tags: list[str] | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_capacity_inputs(self) -> Self:
        capacities = list(self.capacities or [])
        if not capacities:
            raise ValueError("capacities must be provided")
        deduped = list(dict.fromkeys(capacities))
        object.__setattr__(self, "capacities", deduped)
        return self


class DriverFullProfileResponse(BaseSchema):
    """Aggregated driver profile including all related resources in the drivers module."""

    driver: DriverDetailResponse
    documents: DriverDocumentsListResponse
    time_off: DriverTimeOffListResponse
    schedule: WeeklyScheduleResponse
    shifts: DriverShiftListResponse
    traffic_violations: TrafficViolationListResponse


class DriverWithUserCreateResponse(BaseSchema):
    """Response for /add-new-driver: created driver, optional profile photo URL, driving-licence upload result(s)."""

    driver: DriverDetailResponse
    documents: list[DriverDocumentResult] = Field(
        default_factory=list,
        description="Per-document results for the optional driving licence upload (success/failed).",
    )


class DriverSelfProfileResponse(BaseSchema):
    """Driver self-service profile payload."""

    id: str
    user_id: str
    driver_code: str
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    profile_photo_url: str | None = None
    requires_password_change: bool = Field(default=False, description="If true, driver app should redirect to 'Set new password'")
    terms_accepted_at: datetime | None = None
    location_consent_at: datetime | None = None
    map_preference: DriverMapPreference | None = None
    version: int


class DriverSelfOnboardingStatusResponse(BaseSchema):
    terms_accepted: bool = Field(
        description=(
            "Primary gate for **first-time** terms acceptance: false until the driver has completed "
            "``POST …/onboarding-consents`` at least once for the current journey. Use with "
            "``requires_terms_reacceptance`` to decide whether to show the terms UI (initial vs re-accept)."
        ),
    )
    requires_terms_reacceptance: bool = Field(
        default=False,
        description=(
            "Primary gate for **re-acceptance** flows: true when the app must show the terms/consent flow again "
            "even if ``terms_accepted`` is already true — either the active terms **content** no longer matches "
            "the hash stored on the profile (ops updated terms), or (when ``device_installation_id`` / "
            "``X-Device-Installation-Id`` is sent) this install has no audit row for the **current** terms hash "
            "while the profile already accepted terms (e.g. new device). If false, do not block on terms for those reasons."
        ),
    )
    location_consent_given: bool = Field(
        description=(
            "Primary gate for **location** consent: false until location consent was recorded via "
            "``POST …/onboarding-consents``. Use to gate tracking / map features that require explicit consent."
        ),
    )
    terms_accepted_at: datetime | None = None
    location_consent_at: datetime | None = None
    map_preference: DriverMapPreference | None = None


class DriverSelfTermsResponse(BaseSchema):
    id: str
    title: str
    clauses: list["DriverTermsClauseResponse"]
    effective_from: datetime | None = None


class DriverTermsClauseResponse(BaseSchema):
    clause_order: int
    heading: str
    body: str


class DriverTermsAndConditionsResponse(BaseSchema):
    id: str
    title: str
    clauses: list[DriverTermsClauseResponse]
    is_active: bool
    effective_from: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DriverTermsAndConditionsListResponse(BaseSchema):
    items: list[DriverTermsAndConditionsResponse]


class DriverTermsAndConditionsCreateRequest(BaseSchema):
    title: str = Field(..., min_length=1, max_length=255)
    clauses: list[DriverTermsClauseResponse] = Field(..., min_length=1)
    effective_from: datetime | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _validate_clauses(self) -> Self:
        orders = [c.clause_order for c in self.clauses]
        if len(set(orders)) != len(orders):
            raise ValueError("clauses must have unique clause_order values")
        return self


class DriverTermsAndConditionsUpdateRequest(BaseSchema):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    clauses: list[DriverTermsClauseResponse] | None = None
    effective_from: datetime | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _validate_has_fields(self) -> Self:
        if self.title is None and self.clauses is None and self.effective_from is None and self.is_active is None:
            raise ValueError("At least one field must be provided")
        if self.clauses is not None:
            orders = [c.clause_order for c in self.clauses]
            if len(set(orders)) != len(orders):
                raise ValueError("clauses must have unique clause_order values")
        return self


class DriverSelfOnboardingConsentsRequest(BaseSchema):
    accept_terms_and_conditions: bool
    allow_location_access: bool
    # Optional client-reported device context (IP/User-Agent are taken from HTTP headers server-side).
    device_platform: str | None = Field(default=None, max_length=64, description="e.g. ios, android")
    device_model: str | None = Field(default=None, max_length=128)
    app_version: str | None = Field(default=None, max_length=64)
    device_installation_id: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Opaque per-install id the app stores once (e.g. UUID in secure storage). Optional. "
            "When provided with POST onboarding-consents, must be 8–128 characters after trim. "
            "If ``X-Device-Installation-Id`` is also sent, this JSON field wins when both are non-empty after trim."
        ),
    )

    @field_validator("device_installation_id", mode="before")
    @classmethod
    def _strip_device_installation_id(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @model_validator(mode="after")
    def _validate_both_required(self) -> Self:
        if not self.accept_terms_and_conditions:
            raise ValueError("accept_terms_and_conditions must be true")
        if not self.allow_location_access:
            raise ValueError("allow_location_access must be true")
        if self.device_installation_id is not None:
            if len(self.device_installation_id) < 8:
                raise ValueError("device_installation_id must be at least 8 characters when provided")
        return self


class DriverSelfMapPreferenceRequest(BaseSchema):
    map_preference: DriverMapPreference


class DriverSelfProfileUpdateRequest(BaseSchema):
    """Patch body for ``PATCH /v1/driver-profile/me``. Email is not accepted; use admin flows to change email."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
        extra="forbid",
    )

    first_name: str | None = Field(default=None, max_length=100, description="Given name (optional).")
    last_name: str | None = Field(default=None, max_length=100, description="Family name (optional).")
    phone: str | None = Field(default=None, max_length=50, description="Contact number (optional).")
    expected_version: int | None = Field(
        default=None,
        description="Driver row version for optimistic locking; must match current ``version`` when sent.",
    )

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be empty")
        return cleaned

    @field_validator("phone")
    @classmethod
    def validate_phone_format(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Phone cannot be empty")
        # Allow international numbers with optional leading plus and spaces/hyphens.
        if not re.fullmatch(r"^\+?[0-9][0-9\-\s]{6,24}$", cleaned):
            raise ValueError("Phone must be a valid number format")
        return cleaned

    @model_validator(mode="after")
    def validate_has_at_least_one_mutation_field(self) -> Self:
        if self.first_name is None and self.last_name is None and self.phone is None:
            raise ValueError("At least one of first_name, last_name, or phone must be provided")
        return self


class DriverHomeSummaryResponse(BaseSchema):
    addresses_attended: int
    addresses_change_pct: float | None = None
    average_speed_mph: float | None = None
    average_speed_change_pct: float | None = None


class DriverRouteActionResponse(BaseSchema):
    route_id: str
    status: str
    message: str


class DriverStopActionRequest(BaseSchema):
    notes: str | None = Field(default=None, max_length=2000)


class DriverStopActionResponse(BaseSchema):
    stop_id: str
    status: str
    message: str


class DriverTelemetryPoint(BaseSchema):
    route_id: str
    occurred_at: datetime | None = None
    lat: float | None = None
    lng: float | None = None
    speed_mph: float | None = None
    heading: float | None = None
    accuracy_m: float | None = None
    source: str | None = None


class DriverTelemetryBatchRequest(BaseSchema):
    items: list[DriverTelemetryPoint] = Field(default_factory=list, max_length=500)


class DriverTelemetryBatchResponse(BaseSchema):
    accepted: int


class DriverRouteStopEntry(BaseSchema):
    stop_id: str
    sequence: int
    tracking_id: str | None = None
    name: str | None = None
    recipient_phone: str | None = Field(
        default=None,
        description="Recipient phone on the delivery stop (contact at address).",
    )
    tracking_summary: str | None = None
    postal_code: str | None = None
    status: str
    stop_flow_type: str = Field(
        ...,
        description="Per-stop leg: PICKUP, DELIVERY, or RETURN (see ``RouteStopFlowType``).",
    )
    estimated_delivery_time: datetime | None = None
    actual_delivery_time: datetime | None = None
    packages_count: int = 0


class DriverRouteStopsResponse(BaseSchema):
    items: list[DriverRouteStopEntry]


class DriverStopPackageEntry(BaseSchema):
    package_id: str
    status: str


class DriverStopPackagesResponse(BaseSchema):
    route_id: str
    stop_id: str
    tracking_id: str | None = None
    items: list[DriverStopPackageEntry]


class DriverStopPendingPackageEntry(BaseSchema):
    package_id: str
    reference_number: str | None = None
    status: str


class DriverStopPendingPackagesResponse(BaseSchema):
    route_id: str
    stop_id: str
    delivery_stop_id: str
    items: list[DriverStopPendingPackageEntry]


class DriverStopPackageProgressResponse(BaseSchema):
    route_id: str
    stop_id: str
    delivery_stop_id: str
    stop_name: str | None = None
    tracking_id: str | None = None
    stop_flow_type: str = Field(
        ...,
        description="PICKUP, DELIVERY, or RETURN — defines how progress and pending lists are computed.",
    )
    master_label_id: str | None = Field(
        default=None,
        description="Order master label when this is a PICKUP stop (optional one-scan collection).",
    )
    packages_to_scan: int
    scanned_packages: int
    completion_percent: int


class DriverStopNoteImageEntry(BaseSchema):
    id: str
    image_key: str
    sort_order: int


class DriverStopNoteEntry(BaseSchema):
    id: str
    note_type: str = Field(
        description="Same persisted values as admin API: CUSTOMER, PACKAGE_ISSUE_NOTE, ADMIN.",
    )
    message: str = Field(description="Note body.")
    is_blocking: bool = Field(description="May require acknowledgement in the driver app.")
    sort_order: int
    package_ids: list[str] = Field(
        default_factory=list,
        description="Sorted `packages.id` UUIDs for PACKAGE_ISSUE_NOTE; empty otherwise.",
    )
    images: list[DriverStopNoteImageEntry] = Field(default_factory=list)


class DriverStopNotesResponse(BaseSchema):
    route_id: str
    stop_id: str
    delivery_stop_id: str
    notes_hash: str
    requires_acknowledgement: bool
    acknowledged: bool
    acknowledged_at: datetime | None = None
    items: list[DriverStopNoteEntry]


class DriverStopNotesAcknowledgeRequest(BaseSchema):
    notes_hash: str = Field(..., min_length=64, max_length=64)


class DriverStopNotesAcknowledgeResponse(BaseSchema):
    acknowledged: bool
    acknowledged_at: datetime
    notes_hash: str


class DriverStopPackageScanRequest(BaseSchema):
    scan_value: str = Field(..., min_length=1, max_length=120)


class DriverStopPackageScanResponse(BaseSchema):
    package_id: str
    reference_number: str | None = None
    status: str
    matched_by: str | None = Field(
        default=None,
        description="PACKAGE (parcel barcode/id) or MASTER_LABEL (pickup batch confirmation).",
    )
    master_label_id: str | None = Field(default=None, description="Set when ``matched_by`` is MASTER_LABEL.")
    packages_confirmed: int | None = Field(
        default=None,
        description="Parcels moved to collected state when ``matched_by`` is MASTER_LABEL.",
    )


class DriverStopPackageStatusRequest(BaseSchema):
    status: DriverStopPackageFinalStatus = Field(
        ...,
        description=(
            "Final package disposition. **PICKUP** stops must not call this endpoint (scan packages / master label, "
            "then complete the stop).\n\n"
            "* **DELIVERY** — ``DELIVERED_TO_CUSTOMER``, ``LEFT_AT_SAFE_PLACE``, ``CUSTOMER_NOT_HOME``, "
            "``REFUSED_BY_CUSTOMER``.\n"
            "* **RETURN** — ``RETURNED_TO_SENDER``, ``SENDER_NOT_HOME``, ``DISPOSED``."
        ),
    )
    notes: str | None = Field(default=None, max_length=2000)


class DriverStopPackageStatusResponse(BaseSchema):
    package_id: str
    status: str


ReturnStopBatchFinalizeStatus = Literal["RETURNED_TO_SENDER", "SENDER_NOT_HOME", "DISPOSED"]


class DriverStopPackagesBatchStatusRequest(BaseSchema):
    package_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="``packages.id`` UUIDs on this return stop to finalize with the same outcome (duplicates are ignored).",
    )
    status: ReturnStopBatchFinalizeStatus = Field(
        ...,
        description=(
            "Return disposition applied to every listed package. "
            "``RETURNED_TO_SENDER`` is stored as ``RETURNED``; ``SENDER_NOT_HOME`` as ``CUSTOMER_NOT_HOME``."
        ),
    )
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("package_ids", mode="after")
    @classmethod
    def normalize_package_ids(cls, v: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in v:
            s = raw.strip()
            if not s:
                raise ValueError("package_ids must not contain empty strings")
            try:
                normalized.append(str(UuidType(s)))
            except ValueError as err:
                raise ValueError(f"Invalid package_id (must be a UUID): {raw!r}") from err
        deduped = list(dict.fromkeys(normalized))
        if not deduped:
            raise ValueError("package_ids must not be empty after normalization")
        if len(deduped) > 100:
            raise ValueError("At most 100 distinct package_ids allowed")
        return deduped


class DriverStopPackagesBatchStatusItem(BaseSchema):
    package_id: str
    status: str


class DriverStopPackagesBatchStatusResponse(BaseSchema):
    items: list[DriverStopPackagesBatchStatusItem]
    updated_count: int


class DriverStopMissingReportRequest(BaseSchema):
    reason_code: DriverMissingPackageReasonCode
    details: str | None = Field(default=None, max_length=2000)


class DriverStopMissingReportResponse(BaseSchema):
    package_id: str
    status: str
    reason_code: str
    report_id: str


class DriverStopPodUploadUrlRequest(BaseSchema):
    content_type: str = Field(default="image/jpeg", max_length=100)


class DriverStopPodPhotoEntry(BaseSchema):
    id: str
    image_id: str
    image_url: str | None = None
    sort_order: int


class DriverStopPodUploadUrlResponse(BaseSchema):
    delivery_stop_id: str
    items: list[DriverStopPodPhotoEntry]
    photos_count: int


class DriverStopPodPhotoConfirmRequest(BaseSchema):
    image_key: str = Field(..., min_length=1, max_length=255)


class DriverStopPodPhotoConfirmResponse(BaseSchema):
    delivery_stop_id: str
    photos_count: int


class DriverStopPodPhotosResponse(BaseSchema):
    delivery_stop_id: str
    photos_count: int
    items: list[DriverStopPodPhotoEntry]


class DriverStopSignatureRequest(BaseSchema):
    signature_image_key: str = Field(..., min_length=1, max_length=255)
    signature_required: bool | None = None


class DriverStopSignatureResponse(BaseSchema):
    delivery_stop_id: str
    signature_image_key: str
    signature_required: bool


class DriverStopReadinessResponse(BaseSchema):
    route_id: str
    stop_id: str
    delivery_stop_id: str
    stop_flow_type: str = Field(
        ...,
        description="PICKUP, DELIVERY, or RETURN — drives scan/readiness rules for this route stop.",
    )
    master_label_id: str | None = Field(
        default=None,
        description="Order master label when ``stop_flow_type`` is PICKUP (for optional single-scan collection UX).",
    )
    return_requires_pod: bool = Field(
        default=False,
        description=(
            "True on RETURN stops only when any package was finalized as returned-to-sender "
            "(API ``RETURNED_TO_SENDER`` → stored ``RETURNED``): stop-level POD (1–5 photos) is then required before completion. "
            "False for ``SENDER_NOT_HOME``, ``DISPOSED``, or other return terminals."
        ),
    )
    notes_ok: bool
    packages_ok: bool
    pod_ok: bool
    signature_ok: bool
    pending_package_ids: list[str]
    photo_count: int
    signature_required: bool
    notes_hash: str
    acknowledged: bool


class DriverStopReadinessGateNotesResponse(BaseSchema):
    route_id: str
    stop_id: str
    delivery_stop_id: str
    ok: bool = Field(..., description="Same as ``notes_ok`` on ``GET …/readiness``.")
    requires_acknowledgement: bool
    acknowledged: bool
    notes_hash: str


class DriverStopReadinessGatePackagesResponse(BaseSchema):
    route_id: str
    stop_id: str
    delivery_stop_id: str
    stop_flow_type: str
    ok: bool = Field(..., description="Same as ``packages_ok`` on ``GET …/readiness``.")
    pending_package_ids: list[str]


class DriverStopReadinessGatePodResponse(BaseSchema):
    route_id: str
    stop_id: str
    delivery_stop_id: str
    stop_flow_type: str
    ok: bool = Field(..., description="Same as ``pod_ok`` on ``GET …/readiness``.")
    photo_count: int
    return_requires_pod: bool
    stop_pod_required: bool = Field(
        ...,
        description="True when this leg enforces the 1–5 photo rule (delivery, or return with returned-to-sender).",
    )
    min_photos_when_required: int = Field(1, description="Minimum POD photos when ``stop_pod_required``.")
    max_photos_allowed: int = Field(5, description="Inclusive upper bound when POD applies.")


class DriverStopReadinessGateSignatureResponse(BaseSchema):
    route_id: str
    stop_id: str
    delivery_stop_id: str
    stop_flow_type: str
    ok: bool = Field(..., description="Same as ``signature_ok`` on ``GET …/readiness`` (always true on RETURN/PICKUP).")
    signature_required: bool
    captured: bool = Field(..., description="Whether a signature image is stored on the stop POD row.")


class DriverStopCompleteRequest(BaseSchema):
    notes: str | None = Field(default=None, max_length=2000)


class DriverStopCompleteResponse(BaseSchema):
    stop_id: str
    status: str
    message: str
    readiness: DriverStopReadinessResponse


# ── Routes history / telematics ──────────────────────────────────────────────


class RouteHistoryRow(BaseSchema):
    date: date
    route_id: str
    route_code: str
    vehicle_reg: str | None = None
    type: str
    operational_summary: str | None = None
    speeding_count: int
    harsh_braking_count: int


class RouteHistoryResponse(BaseSchema):
    table: PaginatedResponse[RouteHistoryRow]


class DriverRoutesBoardRow(BaseSchema):
    """Single route row for the mobile **All Routes** list (Upcoming / Past tabs)."""

    route_id: str
    route_code: str
    route_type: str
    service_date: date
    vehicle_reg: str | None = None
    status: str = Field(description="Route lifecycle: ASSIGNED, ACTIVE, or COMPLETED for this tab.")
    total_stops: int = 0
    estimated_drive_time_minutes: float | None = None
    actual_drive_time_minutes: float | None = None
    average_route_speed_mph: float | None = Field(
        default=None,
        description="From planned distance and actual drive time when both are present.",
    )
    is_service_date_today: bool = Field(
        default=False,
        description="True when plan service_date equals the driver's depot-local calendar today.",
    )


class DriverRoutesBoardResponse(BaseSchema):
    table: PaginatedResponse[DriverRoutesBoardRow]


class RouteStopSummary(BaseSchema):
    sequence: int
    status: str
    stop_flow_type: str = Field(
        ...,
        description="Per-stop leg: PICKUP, DELIVERY, or RETURN.",
    )
    label: str | None = None
    tracking_id: str | None = None
    lat: float | None = None
    lng: float | None = None
    estimated_arrival: datetime | None = None
    actual_arrival: datetime | None = None


class RouteProgress(BaseSchema):
    completed_stops: int
    total_stops: int
    percent: int


class RouteSummaryResponse(BaseSchema):
    route_id: str
    route_code: str
    date: dt.date | None = None
    status: str
    driver_id: str | None = None
    vehicle_reg: str | None = None
    stops: int
    estimated_drive_time_minutes: float | None = None
    actual_drive_time_minutes: float | None = None
    progress: RouteProgress
    stops_list: list[RouteStopSummary]
    map_points: list[dict[str, object]] = Field(default_factory=list)


class DriverCurrentRouteNextStop(BaseSchema):
    """First non-terminal stop on the route (home / Ready to Drive card)."""

    stop_id: str
    sequence: int
    stop_type: str = Field(
        ...,
        description="Route-level category from ``routes.route_type`` (PICKUP or DELIVERY).",
    )
    stop_flow_type: str = Field(
        ...,
        description="Per-stop operational leg: PICKUP, DELIVERY, or RETURN.",
    )
    location_name: str | None = None
    tracking_id: str | None = None
    scheduled_at: datetime | None = None


class DriverCurrentRouteResponse(BaseSchema):
    route_id: str
    route_code: str
    status: str
    route_type: str
    service_date: date | None = None
    vehicle_reg: str | None = None
    estimated_drive_time_minutes: float | None = None
    actual_drive_time_minutes: float | None = None
    progress: RouteProgress
    todays_deliveries_count: int | None = None
    todays_deliveries_change_pct: float | None = None
    estimated_drive_time_change_pct: float | None = None
    next_stop: DriverCurrentRouteNextStop | None = None


class DriverCurrentRouteData(BaseSchema):
    """Envelope so `data` is always present (open route or explicit null)."""

    current_route: DriverCurrentRouteResponse | None = None


class DriverAssignedRouteRow(BaseSchema):
    route_id: str
    route_code: str
    service_date: date
    route_type: str
    vehicle_reg: str | None = None
    total_stops: int
    status: str


class DriverAssignedRoutesResponse(BaseSchema):
    table: PaginatedResponse[DriverAssignedRouteRow]


class RouteEventEntry(BaseSchema):
    id: str
    route_id: str
    driver_id: str | None = None
    route_code: str | None = None
    event_type: str
    occurred_at: datetime
    location_text: str | None = None
    distance_miles: float | None = None
    speed_mph: float | None = None
    limit_mph: float | None = None
    speed_over_mph: float | None = None
    start_speed_mph: float | None = None
    end_speed_mph: float | None = None
    severity: str | None = None
    lat: float | None = None
    lng: float | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class RouteEventsResponse(BaseSchema):
    table: PaginatedResponse[RouteEventEntry]


class DriverAbove70MphReportResponse(BaseSchema):
    table: PaginatedResponse[RouteEventEntry]


class DriverSharpBrakeReportResponse(BaseSchema):
    table: PaginatedResponse[RouteEventEntry]


class DriverAverageRouteSpeedResponse(BaseSchema):
    route_id: str
    route_code: str
    total_distance_km: float
    actual_drive_time_min: float
    average_speed_mph: float | None = None
    location_points_count: int = 0


class DriverAverageSpeedReportRow(BaseSchema):
    route_id: str
    route_code: str
    service_date: date | None = None
    average_speed_mph: float | None = None
    speed_range_min_mph: float | None = None
    speed_range_max_mph: float | None = None
    severity: Literal["MILD", "MODERATE", "HIGH"] = "MILD"


class DriverAverageSpeedReportResponse(BaseSchema):
    table: PaginatedResponse[DriverAverageSpeedReportRow]


class DriverActiveDriveMapLocation(BaseSchema):
    start_lat: float | None = None
    start_long: float | None = None
    end_lat: float | None = None
    end_long: float | None = None


class DriverActiveDrivingMapVehicle(BaseSchema):
    latitude: float | None = None
    longitude: float | None = None
    recorded_at: datetime | None = Field(
        default=None,
        description="Timestamp of the latest ``LOCATION_PING`` with coordinates on this route.",
    )


class DriverActiveDrivingMapNavigation(BaseSchema):
    encoded_polyline: str | None = Field(
        default=None,
        description=(
            "Cached full-route polyline for this driver's ordered stops (e.g. provider-encoded). "
            "Omitted from response payload when null. May be suppressed when ``meta.polyline_stale`` is true."
        ),
    )
    meta: dict[str, object] | None = Field(
        default=None,
        description="Provider metadata, ``computed_at``, distances, plus ``polyline_stale`` / ``polyline_unverified`` flags.",
    )


class DriverActiveDriveMapStopEntry(BaseSchema):
    stop_id: str
    sequence: int
    stop_flow_type: str
    tracking_id: str | None = None
    location: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    packages_count: int = 0
    status: Literal["COMPLETED", "PENDING", "ONROUTE"] = Field(
        ...,
        description="Stop state for active map card.",
    )


class DriverActiveDrivingMapResponse(BaseSchema):
    location: DriverActiveDriveMapLocation
    vehicle: DriverActiveDrivingMapVehicle
    navigation: DriverActiveDrivingMapNavigation
    data: list[DriverActiveDriveMapStopEntry]


class DriverImportantDeliveryNoteResponse(BaseSchema):
    route_id: str
    stop_id: str
    delivery_stop_id: str
    notes_hash: str
    requires_acknowledgement: bool
    acknowledged: bool
    acknowledged_at: datetime | None = None
    items: list[DriverStopNoteEntry]


class DriverDeliveryDetailResponse(BaseSchema):
    class NoteText(BaseSchema):
        text: str

    class PackageIssue(BaseSchema):
        hasIssue: bool
        description: str | None = None
        thumbnail_image: str | None = None
        images: list[str] = Field(default_factory=list)

    class PackageIssueStopNoteImageEntry(BaseSchema):
        id: str
        image_key: str
        sort_order: int
        image_url: str | None = Field(
            default=None,
            description="Signed image URL when storage signing is configured.",
        )

    class PackageIssueStopNoteEntry(BaseSchema):
        message: str
        package_ids: list[str] = Field(default_factory=list)
        images: list["DriverDeliveryDetailResponse.PackageIssueStopNoteImageEntry"] = Field(
            default_factory=list,
            description="Damage / issue photos attached to this note (same attachments as stop notes list API).",
        )

    class PackagesSummary(BaseSchema):
        totalPackages: int
        totalWeight: str

    class PackageBreakdownEntry(BaseSchema):
        package_id: str
        size: str | None = None
        weight: str | None = None

    class RequirementBlock(BaseSchema):
        required: bool
        message: str

    location: str | None = None
    trackingId: str | None = None
    postalCode: str | None = None
    status: Literal["COMPLETED", "IN-PROGRESS", "PENDING"] = Field(
        ...,
        description="Delivery stop state for detail screen.",
    )
    estimatedDeliveryTime: str | None = None
    actualDeliveryTime: str | None = None
    packagesCount: int = 0
    show_admin_note: bool = False
    show_customer_note: bool = False
    show_package_issue_stop_notes: bool = False
    show_signature_required: bool = False
    show_safe_place_allowed: bool = False
    admin_note: NoteText | None = None
    customer_note: NoteText | None = None
    package_issue_stop_notes: list[PackageIssueStopNoteEntry] = Field(default_factory=list)
    package_issue: PackageIssue
    packages_summary: PackagesSummary
    package_breakdown: list[PackageBreakdownEntry] = Field(default_factory=list)
    signature_required: RequirementBlock
    safe_place_allowed: RequirementBlock


class CalendarSummary(BaseSchema):
    shifts_count: int = 0
    time_off_count: int = 0
    holidays_count: int = 0
    routes_count: int = 0


class CalendarEventEntry(BaseSchema):
    id: str
    source: CalendarEventSource
    title: str
    start_at: datetime
    end_at: datetime
    is_all_day: bool = False
    status: str | None = None
    shift_status: ShiftStatus | None = None
    time_off_type: TimeOffType | None = None
    route_type: RouteType | None = None
    route_status: RouteStatus | None = None
    route_code: str | None = None
    is_paid: bool | None = None
    holiday_name: str | None = None


class DriverCalendarResponse(BaseSchema):
    from_date: date
    to_date: date
    summary: CalendarSummary
    events: list[CalendarEventEntry]


# ── Activity log (admin driver profile) ────────────────────────────────────────


class DriverActivityLogListItem(BaseSchema):
    """Table row: matches Activity Log screen columns + id for detail fetch."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "timestamp": "2026-02-23T08:12:34Z",
                "event": "Login",
                "user_type": "Driver",
                "activity_performed_by": "driver@example.com",
                "ip_address": "192.168.1.45",
            }
        }
    )

    id: str = Field(..., description="Audit row id; use with GET .../activity-log/{audit_log_id} for details.")
    timestamp: datetime = Field(..., description="When the event was recorded (UTC).")
    event: str = Field(..., description="Human-readable event label (e.g. Login, Document upload).")
    user_type: str = Field(..., description="Actor category for UI badge: Admin, Driver, System, etc.")
    activity_performed_by: str | None = Field(
        default=None,
        description="Email of the user who performed the action; null for system-only events.",
    )
    ip_address: str | None = Field(default=None, description="Client IP when the event was recorded.")


class DriverActivityLogListResponse(BaseSchema):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "timestamp": "2026-02-23T08:12:34Z",
                        "event": "Login",
                        "user_type": "Driver",
                        "activity_performed_by": "driver@example.com",
                        "ip_address": "192.168.1.45",
                    }
                ],
                "total": 100,
                "page": 1,
                "size": 50,
            }
        }
    )

    items: list[DriverActivityLogListItem]
    total: int = Field(..., description="Total rows matching filters (all pages).")
    page: int = Field(..., description="Current page (1-based).")
    size: int = Field(..., description="Page size (max 100 on the API).")


class DriverActivityLogDetailResponse(BaseSchema):
    """Full audit entry for row click (values redacted server-side)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "timestamp": "2026-02-23T09:00:00Z",
                "event": "Shift assigned",
                "user_type": "Admin",
                "activity_performed_by": "admin@example.com",
                "ip_address": "10.0.0.1",
                "audit_ref": "AUD-2026-ABCDEF01",
                "action": "driver.shift.create",
                "category": "Fleet",
                "event_type": "SHIFT_CREATED",
                "severity": "NOTICE",
                "entity_type": "driver",
                "entity_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                "entity_ref": None,
                "reason": None,
                "user_id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
                "user_role": "ADMIN",
                "organization_id": None,
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
                "browser": "Google Chrome",
                "device": "Desktop",
                "os": "Windows 11",
                "old_value": None,
                "new_value": {"driver_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901", "status": "PLANNED"},
            }
        }
    )

    id: str
    timestamp: datetime
    event: str
    user_type: str
    activity_performed_by: str | None = None
    ip_address: str | None = None

    audit_ref: str | None = None
    action: str
    category: str | None = None
    event_type: str | None = None
    severity: str
    entity_type: str
    entity_id: str | None = None
    entity_ref: str | None = None
    reason: str | None = None
    user_id: str | None = None
    user_role: str | None = None
    organization_id: str | None = None

    user_agent: str | None = None
    browser: str | None = None
    device: str | None = None
    os: str | None = None

    old_value: dict | None = None
    new_value: dict | None = None
