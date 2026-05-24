from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from app.common.schemas import BaseSchema
from app.modules.vehicle_inspections.enums import (
    ChecklistCategory,
    InspectionResult,
    InspectionStatus,
    InspectionType,
)
from app.modules.vehicles.enums import DefectSeverity, DefectStatus

# Checklist


class ChecklistItem(BaseSchema):
    item: str = Field(min_length=1, max_length=500)
    checked: bool


class ChecklistSection(BaseSchema):
    category: ChecklistCategory
    items: list[ChecklistItem] = Field(min_length=1)


# Create inspection


class CreateInspectionRequest(BaseSchema):
    """Creates the inspection with checklist data. Status starts as IN_PROGRESS."""

    registration_number: str = Field(min_length=1, max_length=20)
    inspection_type: InspectionType = InspectionType.PRE_TRIP
    mileage: float | None = Field(default=None, ge=0)
    checklist: list[ChecklistSection] = Field(min_length=3, max_length=3)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_checklist_sections(self) -> CreateInspectionRequest:
        categories = {s.category for s in self.checklist}
        required = {ChecklistCategory.INSIDE_CABIN, ChecklistCategory.OUTSIDE_VEHICLE, ChecklistCategory.LOAD_EQUIPMENT}
        if categories != required:
            missing = required - categories
            raise ValueError(f"Missing checklist sections: {', '.join(m.value for m in missing)}")
        return self


# Report defect


class ReportInspectionDefectRequest(BaseSchema):
    """Report a defect during inspection. Photos sent as multipart 'images' field."""

    category: str
    severity: DefectSeverity
    description: str | None = Field(default=None, max_length=5000)


# Sign inspection


class SignInspectionRequest(BaseSchema):
    """Finalize the inspection with declaration. Signature sent as multipart 'signature' field."""

    declaration_accepted: bool

    @model_validator(mode="after")
    def must_accept(self) -> SignInspectionRequest:
        if not self.declaration_accepted:
            raise ValueError("Declaration must be accepted to submit the inspection")
        return self


# Responses


class InspectionVehicleSummary(BaseSchema):
    id: str
    registration_number: str | None = None
    make: str | None = None
    model: str | None = None
    fleet_number: str | None = None


class InspectionDriverSummary(BaseSchema):
    id: str
    first_name: str
    last_name: str


class ChecklistSectionStatus(BaseSchema):
    category: ChecklistCategory
    label: str
    completed: bool


class InspectionDefectSummary(BaseSchema):
    id: str
    reference: str
    category: str
    severity: DefectSeverity
    status: DefectStatus
    description: str | None = None
    allowed_to_drive: bool = False
    images: list[str] = []


class InspectionResponse(BaseSchema):
    """Full inspection detail — returned on create, get, and sign."""

    id: str
    vehicle: InspectionVehicleSummary
    driver: InspectionDriverSummary
    inspection_type: InspectionType
    result: InspectionResult | None = None
    status: InspectionStatus
    mileage: float | None = None
    checklist_status: list[ChecklistSectionStatus] = []
    defects: list[InspectionDefectSummary] = []
    declaration_accepted: bool
    signature_url: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    ip_address: str | None = None
    notes: str | None = None
    created_at: datetime


class AssignedVehicleResponse(BaseSchema):
    id: str
    registration_number: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    fleet_number: str | None = None
    fleet_custom_name: str | None = None


class InspectionStatusResponse(BaseSchema):
    """Polling response — driver checks if defects are resolved."""

    inspection_id: str
    status: InspectionStatus
    total_defects: int = 0
    resolved_defects: int = 0
    allowed_to_drive_count: int = 0
    can_proceed: bool = False
