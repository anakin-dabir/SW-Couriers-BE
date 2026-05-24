"""Pydantic schemas for Holidays v1 API."""

from datetime import date

from pydantic import Field

from app.common.schemas import BaseSchema
from app.modules.holidays.enums import HolidayAudience


class HolidayBase(BaseSchema):
    name: str = Field(..., max_length=255)
    start_date: date = Field(..., description="Holiday start date. Internal planning year is derived from this date.")
    end_date: date = Field(..., description="Holiday end date. Must be in the same year as start_date or the next year.")
    audience: HolidayAudience
    allow_shifts: bool
    allowed_driver_ids: list[str] | None = Field(
        default=None,
        description="Driver IDs explicitly allowed to work during this holiday when allow_shifts is true.",
    )


class HolidayCreateRequest(HolidayBase):
    """Create a new holiday."""

    pass


class HolidayUpdateRequest(BaseSchema):
    """Partial update for an existing holiday."""

    name: str | None = Field(default=None, max_length=255)
    start_date: date | None = None
    end_date: date | None = None
    audience: HolidayAudience | None = None
    allow_shifts: bool | None = None
    allowed_driver_ids: list[str] | None = Field(
        default=None,
        description="If provided, replaces the full allowed drivers list. Use empty list to clear.",
    )


class HolidayAllowedDriverInfo(BaseSchema):
    id: str
    name: str


class HolidayResponse(HolidayBase):
    id: str
    allowed_drivers: list[HolidayAllowedDriverInfo] = Field(
        default_factory=list,
        description="Resolved allowed driver records with display names for UI rendering.",
    )


class HolidayListResponse(BaseSchema):
    year: int | None = None
    items: list[HolidayResponse]
    total: int


class CopyHolidaysRequest(BaseSchema):
    source_year: int = Field(..., ge=2000, le=2100, description="Year to copy holidays from.")
    target_year: int = Field(..., ge=2000, le=2100, description="Year to copy holidays into.")


class CopyHolidaysResponse(BaseSchema):
    source_year: int
    target_year: int
    copied_count: int


class HolidayYearSummary(BaseSchema):
    year: int
    holidays_count: int


class HolidayYearSummaryListResponse(BaseSchema):
    items: list[HolidayYearSummary]
    total: int
