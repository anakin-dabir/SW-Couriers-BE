from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.common.schemas import BaseSchema, PaginationParams, UserSchema


class CooldownPeriodResponse(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    months: int
    days: int
    hours: int


class _CooldownWriteRequestBase(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    months: int = Field(default=0, ge=0, le=24)
    days: int = Field(default=0, ge=0, le=365)
    hours: int = Field(default=0, ge=0, le=23)
    reset_to_defaults: bool = False

    @model_validator(mode="after")
    def reset_must_not_include_triplet(self) -> Self:
        if self.reset_to_defaults and self.model_fields_set & {"months", "days", "hours"}:
            raise ValueError(
                "Do not send months, days, or hours when reset_to_defaults is true; send only reset_to_defaults.",
            )
        return self


class PatchGlobalCooldownRequest(_CooldownWriteRequestBase):
    pass


class PostOrgCooldownRequest(_CooldownWriteRequestBase):
    pass


class ActiveCooldownResponse(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    active: bool
    ends_at: str | None
    remaining_seconds: int | None
    summary: str | None


class SetCreditLimitRequest(BaseSchema):
    credit_limit: Decimal = Field(ge=0)
    reason_category: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "Standard category (e.g. BUSINESS_GROWTH, SEASONAL_DEMAND) or any custom label for display and reporting."
        ),
    )
    effective_date: date
    justification: str = Field(min_length=1, max_length=500)

    @field_validator("reason_category")
    @classmethod
    def strip_reason_category(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("reason_category cannot be empty or whitespace")
        return s


class SetPaymentTermsRequest(BaseSchema):
    payment_terms_days: int = Field(ge=1, le=365)
    effective_date: date
    reason: str = Field(min_length=1, max_length=500)
    apply_to_existing_unpaid: bool = False


class TermsHistoryListParams(PaginationParams):
    pass


class TermsHistoryEntryResponse(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    id: str
    date: str
    old_terms: str | None
    new_terms: str | None
    effective_date: str | None
    modified_by: UserSchema | None
    reason: str | None
    applied_to_existing: bool | None
    status: str
    applied_at: str | None


class RiskControlsResponse(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    hold_threshold_pct: int | None


class SetRiskControlsRequest(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    hold_threshold_pct: int = Field(ge=50, le=100)


class CreditLimitSectionSchema(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    total_limit: str | None
    available_credit: str
    utilisation_pct: float | None
    credit_facility_start_date: str | None
    last_updated: str


class CreditTermsSectionSchema(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    payment_terms_days: int | None
    last_updated: str | None


class RiskControlsSectionSchema(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    hold_threshold_pct: int | None


class CooldownSectionSchema(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    months: int
    days: int
    hours: int


class CreditSettingsResponse(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    credit_limit_section: CreditLimitSectionSchema
    credit_terms_section: CreditTermsSectionSchema
    risk_controls_section: RiskControlsSectionSchema
    cooldown_section: CooldownSectionSchema


class CreditLimitHistoryEntryResponse(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    id: str
    date: str
    previous_limit: str | None
    new_limit: str | None
    change_amount: str | None
    change_pct: str | None
    adjustment_type: str | None
    effective_date: str | None
    updated_by: UserSchema | None
    reason_category: str | None
    justification: str | None
    status: str | None = None


class CreditLimitHistoryListParams(PaginationParams):
    pass
