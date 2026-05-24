from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import Field, field_validator, model_validator

from app.common.schemas import BaseSchema, PaginationParams
from app.modules.org_credit.enums import InternalCreditScoreBand, OrgCreditLedgerMovementType


class CreditLedgerListParams(PaginationParams):
    movement_type: OrgCreditLedgerMovementType | None = None


class CreditLedgerEntryResponse(BaseSchema):
    id: str
    created_at: str
    organization_id: str
    account_id: str
    movement_type: str
    source_type: str | None
    source_id: str | None
    idempotency_key: str | None
    used_credit_after: str
    available_credit_after: str
    credit_limit_after: str | None
    adjustment_reason: str | None
    actor_user_id: str | None


class RecalculateCreditSafeRequest(BaseSchema):
    confirmation_text: str = Field(min_length=1, max_length=50)

    @field_validator("confirmation_text")
    @classmethod
    def validate_confirmation(cls, v: str) -> str:
        if v.strip().upper() != "RUN ANOTHER CREDIT CHECK":
            raise ValueError("confirmation_text must be 'RUN ANOTHER CREDIT CHECK'")
        return v


class InternalScoreResponse(BaseSchema):
    current_score: int | None
    label: InternalCreditScoreBand | None
    last_updated: str | None
    score_breakdown: dict[str, Any] | None


class UtilisationCurrentResponse(BaseSchema):
    current_utilisation_pct: float | None
    utilisation_label: str | None
    credit_limit: str | None
    available_credit: str
    outstanding_balance: str
    hold_threshold_pct: int | None


class UtilisationHistoryEntry(BaseSchema):
    id: str
    date: str
    credit_limit: str | None
    outstanding_balance: str
    utilisation_pct: float | None
    available_credit: str
    change: str | None


class UtilisationPaymentBehaviourPlaceholder(BaseSchema):
    summary: str | None = None
    risk_indicator: str | None = None
    trend: str | None = None


class UtilisationAgeingSummaryPlaceholder(BaseSchema):
    as_of: str | None = None
    total_outstanding: str | None = None


class UtilisationAgeingBucketPlaceholder(BaseSchema):
    label: str
    amount: str | None = None
    share_pct: float | None = None


class UtilisationResponse(BaseSchema):
    current: UtilisationCurrentResponse
    history: list[UtilisationHistoryEntry]
    history_total: int
    payment_behaviour: UtilisationPaymentBehaviourPlaceholder
    ageing: UtilisationAgeingSummaryPlaceholder
    ageing_buckets: list[UtilisationAgeingBucketPlaceholder]


class UtilisationHistoryParams(PaginationParams):
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def validate_date_range(self) -> UtilisationHistoryParams:
        if self.date_from is not None and self.date_to is not None and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class TrendDataPoint(BaseSchema):
    period: str
    value: float | None
    change: float | None = None


class InternalScoreTrendDataPoint(BaseSchema):
    period: str
    value: float
    change: float | None = None
    label: InternalCreditScoreBand


class TrendParams(BaseSchema):
    year: int = Field(ge=2020, le=2030)
    granularity: str = Field(default="monthly")
