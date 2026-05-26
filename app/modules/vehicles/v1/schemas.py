from __future__ import annotations

import datetime as dt
from datetime import UTC, date, datetime
from typing import Self
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.common.schemas import BaseResponseSchema, BaseSchema, PaginatedResponse, PaginationParams, SuccessResponse, UserSchema
from app.modules.drivers.v1.schemas import RouteEventEntry, RouteProgress
from app.modules.orders.v1.schemas import StopNoteEntry
from app.modules.planning.enums import RouteType
from app.modules.vehicles.enums import (
    CardDisplayUnit,
    DefectSeverity,
    DefectStatus,
    DocumentType,
    LiveStatus,
    MaintenanceProviderType,
    MotFilterStatus,
    ScheduleCalendarFilterKind,
    ScheduleEventType,
    ServiceBadgeStatus,
    ServiceStatus,
    TaxFilterStatus,
    VehicleAvailability,
    VehicleType,
)

# Vehicle
# All fields mandatory unless explicitly marked optional.
# Optional: preferred_driver_id, depot_id.


class CreateVehicleRequest(BaseSchema):
    registration_number: str = Field(min_length=1, max_length=20)
    fleet_custom_name: str = Field(min_length=1, max_length=100)
    make: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    year: int = Field(ge=1900, le=2100)
    vehicle_type: VehicleType
    fuel_type: str
    cargo_volume_m3: float = Field(ge=0, le=500)
    max_payload_kg: float = Field(ge=0, le=50_000)
    average_mpg: float | None = Field(default=None, ge=0, le=200)
    range_miles: float | None = Field(default=None, ge=0, le=1_000_000)
    current_mileage: int | None = Field(default=None, ge=0, le=2_000_000)
    service_interval_miles: int = Field(ge=100, le=100_000)
    service_interval_months: int = Field(ge=1, le=120)
    max_continuous_driving_hours: float = Field(ge=0, le=24)
    break_duration_minutes: int = Field(ge=0, le=480)

    availability: str = VehicleAvailability.ACTIVE

    initial_maintenance: LogMaintenanceRequest | None = None

    mot_expiry: date | None = None
    tax_due_date: date | None = None
    insurance_expiry: date | None = None
    preferred_driver_id: UUID | None = None
    depot_id: UUID | None = None

    @field_validator("registration_number")
    @classmethod
    def normalize_registration(cls, v: str) -> str:
        return v.upper()

    @field_validator("mot_expiry", "tax_due_date", "insurance_expiry")
    @classmethod
    def expiry_not_in_past(cls, v: date | None) -> date | None:
        if v is not None and v < date.today():
            raise ValueError("Expiry/due date cannot be in the past")
        return v

    @model_validator(mode="after")
    def require_mpg_or_range_by_fuel_type(self) -> CreateVehicleRequest:
        if self.fuel_type == "ELECTRIC":
            if self.range_miles is None:
                raise ValueError("range_miles is required when fuel_type is ELECTRIC")
        else:
            if self.average_mpg is None:
                raise ValueError("average_mpg is required when fuel_type is not ELECTRIC")
        return self

    @model_validator(mode="after")
    def require_initial_maintenance_when_in_maintenance(self) -> CreateVehicleRequest:
        if self.availability == VehicleAvailability.IN_MAINTENANCE and self.initial_maintenance is None:
            raise ValueError("initial_maintenance is required when availability is IN_MAINTENANCE")
        if self.availability != VehicleAvailability.IN_MAINTENANCE and self.initial_maintenance is not None:
            raise ValueError("initial_maintenance must be omitted when availability is not IN_MAINTENANCE")
        return self


class UpdateVehicleSpecsRequest(BaseSchema):
    make: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    fleet_custom_name: str = Field(min_length=1, max_length=100)
    year: int = Field(ge=1900, le=2100)
    fuel_type: str
    cargo_volume_m3: float = Field(ge=0, le=500)
    max_payload_kg: float = Field(ge=0, le=50_000)
    service_interval_miles: int = Field(ge=100, le=100_000)
    service_interval_months: int = Field(ge=1, le=120)
    average_mpg: float | None = Field(default=None, ge=0, le=200)
    range_miles: float | None = Field(default=None, ge=0, le=1_000_000)
    preferred_driver_id: UUID | None = None
    max_continuous_driving_hours: float = Field(ge=0, le=24)
    break_duration_minutes: int = Field(ge=0, le=480)

    @model_validator(mode="after")
    def require_mpg_or_range_by_fuel_type(self) -> UpdateVehicleSpecsRequest:
        if self.fuel_type == "ELECTRIC":
            if self.range_miles is None:
                raise ValueError("range_miles is required when fuel_type is ELECTRIC")
        else:
            if self.average_mpg is None:
                raise ValueError("average_mpg is required when fuel_type is not ELECTRIC")
        return self


class UpdateMileageRequest(BaseSchema):
    new_mileage: int = Field(ge=0, le=2_000_000)


class ChangeAvailabilityRequest(BaseSchema):
    availability: str
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def validate_date_range(self) -> ChangeAvailabilityRequest:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self


class PreferredDriverSummary(BaseSchema):
    id: str
    first_name: str
    last_name: str


class DraftImageItem(BaseSchema):
    id: str
    url: str


class BaseVehicle(BaseResponseSchema):
    registration_number: str
    fleet_number: str
    fleet_custom_name: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    vehicle_type: VehicleType
    fuel_type: str
    cargo_volume_m3: float | None = None
    max_payload_kg: float | None = None
    average_mpg: float | None = None
    range_miles: float | None = None
    current_mileage: int
    service_interval_miles: int | None = None
    service_interval_months: int | None = None
    next_service_due: date | None = None
    max_continuous_driving_hours: float | None = None
    break_duration_minutes: int | None = None
    mot_expiry: date | None = None
    tax_due_date: date | None = None
    insurance_expiry: date | None = None
    depot_id: str | None = None
    preferred_driver: PreferredDriverSummary | None = None
    availability: str
    live_status: LiveStatus
    availability_effective_from: date | None = None
    availability_effective_to: date | None = None
    images: list[DraftImageItem] = []


class CreateVehicleData(BaseVehicle):
    """Flattened vehicle payload with attached upload results."""

    documents: list[DocumentResponse] = []


class CreateVehicleResponse(SuccessResponse[CreateVehicleData]):
    model_config = ConfigDict(extra="forbid")

    failed_documents: list[FileUploadFailure] = []
    failed_images: list[FileUploadFailure] = []


class VehicleResponse(BaseVehicle):
    model_config = ConfigDict(extra="forbid")

    next_service_card: InfoCard | None = None
    current_mileage_card: InfoCard | None = None
    efficiency_card: InfoCard | None = None


class UpdateVehicleSpecsResponse(SuccessResponse[VehicleResponse]):
    model_config = ConfigDict(extra="forbid")

    failed_images: list[FileUploadFailure] = []


# Draft


class SaveDraftRequest(BaseSchema):
    """POST draft: max_continuous_driving_hours, break_duration_minutes, and availability rules apply via validators."""

    registration_number: str | None = Field(default=None, min_length=1, max_length=20)
    fleet_custom_name: str | None = Field(default=None, min_length=1, max_length=100)
    make: str | None = Field(default=None, min_length=1, max_length=100)
    model: str | None = Field(default=None, min_length=1, max_length=100)
    year: int | None = Field(default=None, ge=1900, le=2100)
    vehicle_type: VehicleType | None = None
    fuel_type: str | None = None
    cargo_volume_m3: float | None = Field(default=None, ge=0, le=500)
    max_payload_kg: float | None = Field(default=None, ge=0, le=50_000)
    average_mpg: float | None = Field(default=None, ge=0, le=200)
    range_miles: float | None = Field(default=None, ge=0, le=1_000_000)
    current_mileage: int | None = Field(default=None, ge=0, le=2_000_000)
    service_interval_miles: int | None = Field(default=None, ge=100, le=100_000)
    service_interval_months: int | None = Field(default=None, ge=1, le=120)
    max_continuous_driving_hours: float | None = Field(default=None, ge=0, le=24)
    break_duration_minutes: int | None = Field(default=None, ge=0, le=480)
    mot_expiry: date | None = None
    tax_due_date: date | None = None
    insurance_expiry: date | None = None
    preferred_driver_id: UUID | None = None
    depot_id: UUID | None = None
    availability: str | None = None
    initial_maintenance: LogMaintenanceRequest | None = None

    @field_validator("registration_number")
    @classmethod
    def normalize_registration(cls, v: str | None) -> str | None:
        if v is not None:
            return v.upper()
        return v

    @field_validator("mot_expiry", "tax_due_date", "insurance_expiry")
    @classmethod
    def expiry_not_in_past(cls, v: date | None) -> date | None:
        if v is not None and v < date.today():
            raise ValueError("Expiry/due date cannot be in the past")
        return v

    @model_validator(mode="after")
    def require_mpg_or_range_when_fuel_type_in_request(self) -> SaveDraftRequest:
        if "fuel_type" not in self.model_fields_set or self.fuel_type is None:
            return self
        if self.fuel_type == "ELECTRIC":
            if self.range_miles is None:
                raise ValueError("range_miles is required when fuel_type is ELECTRIC")
        else:
            if self.average_mpg is None:
                raise ValueError("average_mpg is required when fuel_type is not ELECTRIC")
        return self

    @model_validator(mode="after")
    def require_initial_maintenance_when_in_maintenance(self) -> SaveDraftRequest:
        if type(self).__name__ == "UpdateDraftRequest":
            avail = self.availability
            if avail is None:
                return self
        else:
            avail = self.availability or VehicleAvailability.ACTIVE
        if avail == VehicleAvailability.IN_MAINTENANCE and self.initial_maintenance is None:
            raise ValueError("initial_maintenance is required when availability is IN_MAINTENANCE")
        if avail != VehicleAvailability.IN_MAINTENANCE and self.initial_maintenance is not None:
            raise ValueError("initial_maintenance must be omitted when availability is not IN_MAINTENANCE")
        return self

    @model_validator(mode="after")
    def at_least_one_field(self) -> SaveDraftRequest:
        if type(self).__name__ == "UpdateDraftRequest":
            return self
        if not any(v is not None for v in self.model_dump(exclude_unset=True).values()):
            raise ValueError("At least one field must be provided to save a draft")
        return self


class UpdateDraftRequest(SaveDraftRequest):
    """Partial update — all fields optional. Empty object is allowed since the
    request may only carry deleted_image_ids / deleted_document_ids / new files.
    On publish, availability + initial_maintenance are read from here."""


class DraftVehicleData(BaseSchema):
    """Draft response payload — vehicle fields plus draft metadata."""

    id: str
    draft_number: str
    vehicle_id: str
    registration_number: str | None = None
    fleet_number: str | None = None
    fleet_custom_name: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    vehicle_type: VehicleType | None = None
    fuel_type: str | None = None
    cargo_volume_m3: float | None = None
    max_payload_kg: float | None = None
    average_mpg: float | None = None
    range_miles: float | None = None
    current_mileage: int | None = None
    service_interval_miles: int | None = None
    service_interval_months: int | None = None
    max_continuous_driving_hours: float | None = None
    break_duration_minutes: int | None = None
    mot_expiry: date | None = None
    tax_due_date: date | None = None
    insurance_expiry: date | None = None
    preferred_driver_id: str | None = None
    depot_id: str | None = None
    availability: str | None = None
    initial_maintenance: LogMaintenanceRequest | None = None
    images: list[DraftImageItem] = []
    documents: list[DocumentResponse] = []


class SaveDraftResponse(SuccessResponse[DraftVehicleData]):
    model_config = ConfigDict(extra="forbid")

    failed_documents: list[FileUploadFailure] = []
    failed_images: list[FileUploadFailure] = []


class DraftListItem(BaseSchema):
    id: str
    draft_number: str
    vehicle_id: str
    registration_number: str | None = None
    fleet_number: str | None = None
    fleet_custom_name: str | None = None
    preferred_driver: PreferredDriverSummary | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    vehicle_type: VehicleType | None = None
    fuel_type: str | None = None
    average_mpg: float | None = None
    range_miles: float | None = None
    cargo_volume_m3: float | None = None
    max_payload_kg: float | None = None
    service_interval_miles: int | None = None
    service_interval_months: int | None = None
    max_continuous_driving_hours: float | None = None
    break_duration_minutes: int | None = None
    availability: str
    last_edited: datetime


class DraftListParams(PaginationParams):
    order_desc: bool = Field(default=True, description="Sort by created_at descending (newest first) when true, ascending (oldest first) when false")
    search: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description=("Case-insensitive substring match on linked draft vehicle: registration, fleet number, " "make, model, year (as text), vehicle type"),
    )


class InfoCard(BaseSchema):
    display_unit: CardDisplayUnit
    display_value: int | None = None


class VehicleListItem(BaseSchema):
    id: str
    registration_number: str
    fleet_number: str
    make: str | None = None
    model: str | None = None
    year: int | None = None
    live_status: LiveStatus
    availability: str
    tax: TaxComplianceBadge
    mot: MotComplianceBadge
    service: ServiceBadge
    defects: DefectsSummary
    images: list[str] = []


class DeleteVehicleRequest(BaseSchema):
    reason: str = Field(min_length=1, max_length=2000)


class DeletedByUser(BaseSchema):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None


class DeletedVehicleListItem(BaseSchema):
    id: str
    registration_number: str | None = None
    make: str | None = None
    model: str | None = None
    vehicle_type: VehicleType | None = None
    deletion_reason: str
    created_at: datetime
    deleted_by: DeletedByUser | None = None


class DeletedVehicleListParams(PaginationParams):
    pass


class TaxComplianceBadge(BaseSchema):
    status: TaxFilterStatus
    remaining_days: int | None = None
    due_date: date | None = None


class MotComplianceBadge(BaseSchema):
    status: MotFilterStatus
    remaining_days: int | None = None
    due_date: date | None = None


class ServiceBadge(BaseSchema):
    status: ServiceBadgeStatus
    display_unit: CardDisplayUnit
    display_value: int | None = None


class DefectsSummary(BaseSchema):
    total: int = 0
    pending: int = 0
    in_progress: int = 0


class VehicleListParams(PaginationParams):
    search: str | None = Field(default=None, min_length=1, max_length=100, description="Search by registration (stored uppercase); LIKE %search%")
    status: list[LiveStatus] | None = Field(
        default=None,
        min_length=1,
        description="Live status filter (multi-select)",
    )
    availability: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Vehicle availability filter (multi-select)",
    )
    mot_status: list[MotFilterStatus] | None = Field(default=None, min_length=1)
    tax_status: list[TaxFilterStatus] | None = Field(default=None, min_length=1)


class FleetStatsResponse(BaseSchema):
    total_vehicles: int
    active_vehicles: int
    in_maintenance: int
    compliance_alerts: int


# Compliance


class CompliancePercentageBar(BaseSchema):
    validity_used: int = Field(ge=0, le=100)
    remaining: int = Field(ge=0, le=100)


class ComplianceCertificateItemResponse(BaseSchema):
    """MOT / insurance style: VALID, EXPIRING_SOON, EXPIRED, MISSING."""

    status: MotFilterStatus
    expiry_date: str | None = None
    remaining_days: int | None = None
    reference_number: str | None = None
    provider: str | None = None
    percentage_bar: CompliancePercentageBar | None = None


class ComplianceTaxItemResponse(BaseSchema):
    """Road tax style: PAID, DUE_SOON, OVERDUE, MISSING."""

    status: TaxFilterStatus
    due_date: str | None = None
    remaining_days: int | None = None
    percentage_bar: CompliancePercentageBar | None = None


class ComplianceServiceIntervalItemResponse(BaseSchema):
    status: ServiceBadgeStatus
    expiry_date: str | None = None
    remaining_days: int | None = None
    remaining_miles: int | None = None
    display_unit: CardDisplayUnit | None = None
    display_value: int | None = None
    percentage_bar: CompliancePercentageBar | None = None


class ComplianceSummaryResponse(BaseSchema):
    mot: ComplianceCertificateItemResponse
    tax: ComplianceTaxItemResponse
    insurance: ComplianceCertificateItemResponse
    service_interval: ComplianceServiceIntervalItemResponse


# Maintenance
# Optional: date_to, notes.


class LogMaintenanceRequest(BaseSchema):
    maintenance_types: list[str] = Field(min_length=1, max_length=10)
    provider_type: MaintenanceProviderType
    date_from: date
    cost: float = Field(ge=0, le=1_000_000)
    date_to: date | None = None
    notes: str | None = Field(default=None, max_length=2000)
    garage: str = Field(min_length=1, max_length=255)

    @field_validator("date_from")
    @classmethod
    def date_from_not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("date_from cannot be in the future")
        return v

    @model_validator(mode="after")
    def validate_date_range(self) -> LogMaintenanceRequest:
        if self.date_to is not None and self.date_to < self.date_from:
            raise ValueError("date_to must be on or after date_from")
        return self


class UpdateMaintenanceRecordRequest(BaseSchema):
    maintenance_types: list[str] | None = Field(default=None, min_length=1, max_length=10)
    provider_type: MaintenanceProviderType | None = None
    date_from: date | None = None
    date_to: date | None = None
    cost: float | None = Field(default=None, ge=0, le=1_000_000)
    notes: str | None = Field(default=None, max_length=2000)
    garage: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("date_from")
    @classmethod
    def date_from_not_future(cls, v: date | None) -> date | None:
        if v is None:
            return v
        if v > date.today():
            raise ValueError("date_from cannot be in the future")
        return v

    @model_validator(mode="after")
    def at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        return self


class MaintenanceRecordResponse(BaseSchema):
    id: str
    reference: str
    vehicle_id: str
    maintenance_types: list[str]
    provider_type: MaintenanceProviderType
    date_from: date
    date_to: date | None = None
    cost: float | None = None
    notes: str | None = None
    garage: str
    recorded_by_id: str | None = None
    created_at: datetime
    updated_at: datetime


class MaintenanceCostByTypeItem(BaseSchema):
    maintenance_type: str
    cost: float
    percentage: float


class MaintenanceCostSummaryResponse(BaseSchema):
    vehicle_id: str
    total_cost: float
    by_type: list[MaintenanceCostByTypeItem] = Field(default_factory=list, description="Cost and percentage per maintenance type")


class MaintenanceListParams(PaginationParams):
    maintenance_type: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Workshop maintenance type filter (multi-select): OIL_CHANGE, REPAIR, MOT, TYRE_REPLACEMENT, INSPECTION, BODYWORK.",
    )
    search: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Search maintenance records by reference (MT- plus digits), garage name, or service type.",
    )


# Schedule & availability (calendar view; delivery events from other services)


class ScheduleEventDetails(BaseSchema):
    maintenance_id: str | None = None
    maintenance_reference: str | None = None
    maintenance_types: list[str] | None = None
    maintenance_description: str | None = None
    route_id: str | None = None
    route_code: str | None = None
    route_type: str | None = None
    status_label: str | None = None
    driver_name: str | None = None
    stops_count: int | None = None
    extra: dict[str, str | int | float | bool] | None = None


class ScheduleEvent(BaseSchema):
    date: date
    type: ScheduleEventType
    details: ScheduleEventDetails | None = None


class UtilizationSummary(BaseSchema):
    completed_delivery_days: int = 0
    completed_delivery_percent: int = 0
    completed_pickup_days: int = 0
    completed_pickup_percent: int = 0
    out_for_delivery_days: int = 0
    out_for_delivery_percent: int = 0
    out_for_pickup_days: int = 0
    out_for_pickup_percent: int = 0
    maintenance_days: int = 0
    maintenance_percent: int = 0
    unavailable_days: int = 0
    unavailable_percent: int = 0
    available_days: int = 0
    available_percent: int = 0


class ScheduleParams(BaseSchema):
    start_date: date = Field(description="Start of date range (inclusive)")
    end_date: date = Field(description="End of date range (inclusive)")
    event_types: list[ScheduleCalendarFilterKind] | None = Field(
        default=None,
        description="If set, only days matching these calendar categories are returned (each category maps to one or more "
        "`ScheduleEventType` values in the payload). `DELIVERY_ROUTE` → completed and in-progress delivery; "
        "`PICKUP_ROUTE` → completed and in-progress pickup; `MAINTENANCE`; `UNAVAILABLE`. Omit to return all days. "
        "Utilization summary is always for the full range.",
    )

    @model_validator(mode="after")
    def validate_date_range(self) -> ScheduleParams:
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        if (self.end_date - self.start_date).days > 366:
            raise ValueError("Date range must not exceed 366 days")
        return self


class ScheduleResponse(BaseSchema):
    events: list[ScheduleEvent]
    utilization_summary: UtilizationSummary


class VehicleRouteHistoryParams(PaginationParams):
    type: list[RouteType] | None = Field(
        default=None,
        min_length=1,
        description="Route type filter (multi-select)",
    )
    search: str | None = Field(default=None, max_length=200)


class VehicleRouteHistoryRow(BaseSchema):
    date: date
    route_id: str
    route_code: str
    driver_name: str | None = None
    type: str
    estimated_miles: float | None = Field(
        default=None,
        description="Planned / recorded distance for the route in miles when ``total_distance_km`` is set (converted).",
    )


class VehicleRouteHistoryResponse(BaseSchema):
    table: PaginatedResponse[VehicleRouteHistoryRow]


class VehicleRouteTelemetryEventsResponse(BaseSchema):
    items: list[RouteEventEntry]


class VehicleRouteStopNotesBucket(BaseSchema):
    route_stop_id: str
    sequence: int
    delivery_stop_id: str | None = None
    notes: list[StopNoteEntry] = Field(default_factory=list)


class VehicleRouteNotesResponse(BaseSchema):
    route_id: str
    stops: list[VehicleRouteStopNotesBucket] = Field(default_factory=list)


class VehicleRouteTelemetrySummary(BaseSchema):
    speeding_events: int = 0
    harsh_braking_events: int = 0
    max_speed_mph: float | None = None
    average_speed_mph: float | None = None


class VehicleRouteDetailResponse(BaseSchema):
    route_id: str
    route_code: str
    route_type: str
    date: dt.date | None = None
    status: str
    driver_id: str | None = None
    driver_name: str | None = None
    vehicle_reg: str | None = None
    estimated_miles: float | None = None
    stops: int
    estimated_drive_time_minutes: float | None = None
    actual_drive_time_minutes: float | None = None
    progress: RouteProgress
    telemetry: VehicleRouteTelemetrySummary
    encoded_polyline: str | None = Field(
        default=None,
        description="Planned route navigation polyline when present on the route row.",
    )


class VehicleRouteStopsListParams(PaginationParams):
    pass


class VehicleRouteStopListRow(BaseSchema):
    route_stop_id: str
    sequence: int
    stop_flow_type: str
    status: str
    tracking_id: str | None = None
    label: str | None = Field(
        default=None,
        description="Short location line (postcode – place) for table display.",
    )
    estimated_arrival: datetime | None = None
    actual_arrival: datetime | None = None
    notes_count: int = 0


class VehicleRouteStopsListResponse(BaseSchema):
    table: PaginatedResponse[VehicleRouteStopListRow]


class VehicleRouteStopPackageItem(BaseSchema):
    id: str
    package_id: str
    status: str
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    weight_kg: float | None = None


class VehicleRouteStopDetailResponse(BaseSchema):
    route_id: str
    route_stop_id: str
    stop_flow_type: str
    sequence: int
    status: str
    tracking_id: str | None = None
    location_label: str | None = None
    postcode: str | None = None
    order_id: str | None = None
    delivery_stop_id: str | None = None
    scheduled_at: datetime | None = None
    actual_at: datetime | None = None
    total_packages: int = 0
    total_weight_kg: float | None = None
    packages: list[VehicleRouteStopPackageItem] = Field(default_factory=list)


# Defects
# Optional: route_id, reported_by_id. Images: multipart field "images" on POST report defect.


class ReportDefectRequest(BaseSchema):
    reported_at: datetime
    category: str
    severity: DefectSeverity
    description: str = Field(min_length=1, max_length=5000)
    status: DefectStatus = DefectStatus.PENDING
    allowed_to_drive: bool = False
    route_id: str | None = Field(default=None, min_length=1, max_length=50)
    reported_by_id: UUID | None = None

    @field_validator("reported_at")
    @classmethod
    def not_future(cls, v: datetime) -> datetime:

        now = datetime.now(UTC) if v.tzinfo else datetime.now()
        if v > now:
            raise ValueError("reported_at cannot be in the future")
        return v


class UpdateDefectRequest(BaseSchema):
    status: DefectStatus | None = None
    allowed_to_drive: bool | None = None
    category: str | None = None
    severity: DefectSeverity | None = None
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    route_id: str | None = Field(default=None, min_length=1, max_length=50)
    reported_by_id: UUID | None = None
    reported_at: datetime | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        return self

    @field_validator("reported_at")
    @classmethod
    def reported_at_not_future(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return v
        now = datetime.now(UTC) if v.tzinfo else datetime.now()
        if v > now:
            raise ValueError("reported_at cannot be in the future")
        return v


class DefectResponse(BaseResponseSchema):
    vehicle_id: str
    reference: str
    route_id: str | None = None
    reported_by: UserSchema | None = None
    reported_at: datetime
    category: str
    severity: DefectSeverity
    status: DefectStatus
    description: str | None = None
    images: list[str] | None = None
    allowed_to_drive: bool


class DefectListParams(PaginationParams):
    status: list[DefectStatus] | None = Field(
        default=None,
        min_length=1,
        description="Defect workflow status (multi-select): PENDING, IN_PROGRESS, RESOLVED.",
    )
    search: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description=(
            "Case-insensitive substring match on defect reference (DF- plus digits), reporter first name, last name, or full name "
            "(outer join on reported_by_id), or route_id. Use the status filter for workflow status."
        ),
    )


# Service Records
# Optional: notes.


class AddServiceRecordRequest(BaseSchema):
    service_date: date
    service_type: str
    next_service_due: date | None = None
    mileage_at_service: int | None = Field(default=None, ge=0, le=2_000_000)
    cost: float = Field(ge=0, le=1_000_000)
    status: ServiceStatus = ServiceStatus.COMPLETED
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("service_date")
    @classmethod
    def service_date_not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("service_date cannot be in the future")
        return v

    @model_validator(mode="after")
    def validate_next_after_service(self) -> Self:
        if self.next_service_due is not None and self.next_service_due <= self.service_date:
            raise ValueError("next_service_due must be after service_date")
        return self


class UpdateServiceRecordRequest(BaseSchema):
    service_date: date | None = None
    service_type: str | None = None
    next_service_due: date | None = None
    mileage_at_service: int | None = Field(default=None, ge=0, le=2_000_000)
    cost: float | None = Field(default=None, ge=0, le=1_000_000)
    status: ServiceStatus | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("service_date")
    @classmethod
    def service_date_not_future(cls, v: date | None) -> date | None:
        if v is not None and v > date.today():
            raise ValueError("service_date cannot be in the future")
        return v

    @model_validator(mode="after")
    def at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        return self


class ServiceRecordResponse(BaseSchema):
    id: str
    vehicle_id: str
    service_date: date
    service_type: str
    next_service_due: date | None = None
    mileage_at_service: int | None = None
    cost: float | None = None
    status: ServiceStatus
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


# Documents


class UploadDocumentRequest(BaseSchema):
    document_type: DocumentType
    title: str | None = Field(default=None, max_length=255)
    expiry_date: date
    reference_number: str = Field(min_length=1, max_length=100)
    provider: str | None = Field(default=None, max_length=200)

    @field_validator("expiry_date")
    @classmethod
    def expiry_not_in_past(cls, v: date) -> date:
        if v < date.today():
            raise ValueError("expiry_date cannot be in the past")
        return v

    @model_validator(mode="after")
    def validate_title_for_other(self) -> UploadDocumentRequest:
        if self.document_type == DocumentType.OTHER and (self.title is None or not self.title.strip()):
            raise ValueError("title is required when document_type is OTHER")
        return self


class UpdateDocumentMetadataRequest(BaseSchema):
    """Patch metadata on an existing document without re-uploading the file."""

    id: str = Field(description="ID of the existing document to update")
    document_type: DocumentType | None = None
    title: str | None = Field(default=None, max_length=255)
    expiry_date: date | None = None
    reference_number: str | None = Field(default=None, min_length=1, max_length=100)
    provider: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def at_least_one_field(self) -> UpdateDocumentMetadataRequest:
        fields = self.model_dump(exclude={"id"}, exclude_unset=True)
        if not any(v is not None for v in fields.values()):
            raise ValueError("At least one metadata field must be provided besides id")
        return self


class DocumentResponse(BaseSchema):
    id: str
    document_type: DocumentType
    title: str | None = None
    url: str | None = None
    expiry_date: date | None = None
    reference_number: str | None = None
    provider: str | None = None
    created_at: datetime


class VehicleDocOTPSendResponse(BaseSchema):
    message: str = "OTP sent to your registered email address. It expires in 10 minutes."


class VehicleDocOTPVerifyRequest(BaseSchema):
    otp: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit numeric OTP received by email.",
    )


class VehicleDocAccessTokenResponse(BaseSchema):
    vehicle_doc_access_token: str = Field(
        ...,
        description=(
            "Pass this token as the `X-Vehicle-Doc-Access-Token` header when listing or deleting vehicle documents. "
            "Uploads use the normal admin JWT only. Valid for 1 hour from issue time."
        ),
    )
    expires_in: int = Field(3600, description="Seconds until the token expires.")
    expires_at: datetime = Field(..., description="UTC datetime when the token expires.")
    message: str


# Vehicle Images


class VehicleImageResponse(BaseSchema):
    id: str
    vehicle_id: str
    url: str
    created_at: datetime


class BulkUploadFailureItem(BaseSchema):
    index: int
    message: str


class VehicleImageUrlListResponse(SuccessResponse[list[str]]):
    """GET /vehicles/{id}/images — ``data`` is signed URL strings only."""


class VehicleImageUploadResponse(SuccessResponse[list[str]]):
    """POST /vehicles/{id}/images — ``data`` is URLs for successfully stored images."""

    model_config = ConfigDict(extra="forbid")
    failed_images: list[BulkUploadFailureItem] = Field(default_factory=list)


class ReportDefectUploadResponse(SuccessResponse[DefectResponse]):
    """POST /vehicles/{id}/defects — defect includes ``images`` (URLs); partial image failures in ``failed_images``."""

    model_config = ConfigDict(extra="forbid")
    failed_images: list[BulkUploadFailureItem] = Field(default_factory=list)


# Vehicle creation response


class FileUploadFailure(BaseSchema):
    """Describes a single file that failed during upload."""

    index: int = Field(description="Zero-based index of the file in the uploaded list")
    filename: str = Field(description="Original filename submitted by the client")
    reason: str = Field(description="Human-readable error message")
