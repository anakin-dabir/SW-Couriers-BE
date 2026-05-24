"""Team availability API schemas (admin settings)."""

from __future__ import annotations

from datetime import date

from pydantic import Field, ValidationInfo, field_validator, model_validator

from app.common.schemas import BaseSchema
from app.modules.drivers.enums import TimeOffType
from app.modules.team_availability.enums import LeavePaymentStatus, TeamMemberType


class LeaveTypeOption(BaseSchema):
    type: TimeOffType
    label: str
    color_hex: str = Field(description="Hex colour for calendar chips and filters")


class LeaveTypeListResponse(BaseSchema):
    items: list[LeaveTypeOption]


class TeamCalendarLeaveEntry(BaseSchema):
    id: str = Field(description="driver_time_off.id or staff_time_off.id")
    member_type: TeamMemberType = TeamMemberType.DRIVER
    calendar_date: date
    source: str = "TIME_OFF"
    driver_id: str | None = None
    driver_code: str | None = None
    user_id: str | None = None
    short_name: str = Field(description="Abbreviated label for month grid, or 'You' for current user")
    display_name: str
    profile_photo_url: str | None = None
    time_off_type: TimeOffType
    leave_type_label: str
    color_hex: str
    start_date: date
    end_date: date
    is_paid: bool
    is_current_user: bool = False


class TeamCalendarHolidayEntry(BaseSchema):
    id: str
    calendar_date: date
    source: str = "HOLIDAY"
    holiday_name: str
    start_date: date
    end_date: date
    audience: str | None = None


class TeamCalendarSummary(BaseSchema):
    drivers_on_leave_count: int = 0
    staff_on_leave_count: int = 0
    leave_day_entries_count: int = 0
    holiday_day_entries_count: int = 0


class TeamCalendarResponse(BaseSchema):
    from_date: date
    to_date: date
    summary: TeamCalendarSummary
    leave_entries: list[TeamCalendarLeaveEntry]
    holiday_entries: list[TeamCalendarHolidayEntry]


class WhoIsOffItem(BaseSchema):
    time_off_id: str
    member_type: TeamMemberType = TeamMemberType.DRIVER
    driver_id: str | None = None
    driver_code: str | None = None
    user_id: str | None = None
    display_name: str
    profile_photo_url: str | None = None
    time_off_type: TimeOffType
    leave_type_label: str
    color_hex: str
    start_date: date
    end_date: date
    duration_days: int
    is_current_user: bool = False


class WhoIsOffResponse(BaseSchema):
    from_date: date
    to_date: date
    items: list[WhoIsOffItem]
    total: int


class TeamLeaveDetailResponse(BaseSchema):
    id: str
    member_type: TeamMemberType = TeamMemberType.DRIVER
    driver_id: str | None = None
    driver_code: str | None = None
    user_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    profile_photo_url: str | None = None
    start_date: date
    end_date: date
    duration_days: int
    duration_label: str = Field(description="e.g. Full Day, 10 Days")
    type: TimeOffType
    leave_type_label: str
    color_hex: str
    leave_status: LeavePaymentStatus
    notes: str | None = None
    is_paid: bool


class MyLeaveCreateRequest(BaseSchema):
    start_date: date
    end_date: date
    type: TimeOffType
    is_paid: bool = True
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("end_date")
    @classmethod
    def end_not_before_start(cls, end_date: date, info: ValidationInfo) -> date:
        start = info.data.get("start_date")
        if start is not None and end_date < start:
            raise ValueError("end_date cannot be before start_date")
        return end_date


class MyLeaveUpdateRequest(BaseSchema):
    start_date: date | None = None
    end_date: date | None = None
    type: TimeOffType | None = None
    is_paid: bool | None = None
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def end_not_before_start(self) -> MyLeaveUpdateRequest:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        return self


class MyLeaveItem(BaseSchema):
    id: str
    start_date: date
    end_date: date
    type: TimeOffType
    leave_type_label: str
    color_hex: str
    days: int
    duration_label: str
    leave_status: LeavePaymentStatus
    is_paid: bool
    notes: str | None = None
    can_edit: bool = True
    can_delete: bool = True


class MyLeaveListResponse(BaseSchema):
    items: list[MyLeaveItem]
    paid_leave_taken: int = Field(description="Paid leave days taken in the current calendar year")
    unpaid_leave_taken: int = Field(description="Unpaid leave days taken in the current calendar year")
    total: int
