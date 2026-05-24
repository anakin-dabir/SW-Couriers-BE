from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from app.common.schemas import BaseSchema, PaginationParams, UserSchema
from app.modules.org_credit.enums import OrgCreditReviewFrequency
from app.modules.org_credit.v1.schemas import CreditReportResponse
from app.modules.org_credit_reviews.enums import (
    CreditReviewOutcome,
    CreditReviewReminderPeriod,
    CreditReviewRiskLevel,
)


class CreditSnapshotSchema(BaseSchema):
    status: str
    credit_limit: str | None
    last_review_date: str | None
    utilization_percent: float | None
    next_review_due: str | None
    risk_level: str | None


class CreditReviewResponse(BaseSchema):
    id: str
    organization_id: str
    account_id: str
    reviewer: UserSchema | None = None
    review_date: str
    review_frequency_at_time: str | None
    risk_level: str
    outcome: str
    review_notes: str | None
    next_review_frequency: str | None
    recommended_new_limit: str | None
    recommended_payment_terms_days: int | None
    created_at: str
    updated_at: str


class CreditReviewDetailResponse(CreditReviewResponse):
    creditsafe: CreditReportResponse | None = None


class CreditReviewHistoryItem(BaseSchema):
    id: str
    review_date: str
    review_frequency_at_time: str | None
    reviewer: UserSchema | None = None
    risk_level: str
    outcome: str
    review_notes: str | None


class OrgCreditReviewsAndStatusResponse(BaseSchema):
    snapshot: CreditSnapshotSchema | None


class SubmitReviewRequest(BaseSchema):
    risk_level: CreditReviewRiskLevel
    outcome: CreditReviewOutcome
    review_notes: str | None = Field(default=None, max_length=2000)
    next_review_frequency: OrgCreditReviewFrequency | None = None
    recommended_new_limit: Decimal | None = Field(default=None, ge=0)
    recommended_payment_terms_days: int | None = Field(default=None, ge=1, le=365)
    credit_report_id: str | None = Field(default=None, min_length=36, max_length=36)

    @model_validator(mode="after")
    def _require_fields_for_outcome(self) -> Self:
        if self.outcome in (CreditReviewOutcome.INCREASE_LIMIT, CreditReviewOutcome.DECREASE_LIMIT):
            if self.recommended_new_limit is None:
                raise ValueError("recommended_new_limit is required for limit change outcomes.")
        if self.outcome in (CreditReviewOutcome.EXTEND_TERMS, CreditReviewOutcome.SHORTEN_TERMS):
            if self.recommended_payment_terms_days is None:
                raise ValueError("recommended_payment_terms_days is required for terms change outcomes.")
        return self


class ReviewConfigurationRequest(BaseSchema):
    review_frequency: OrgCreditReviewFrequency
    next_review_date: date
    reminder_period: CreditReviewReminderPeriod
    reviewer_user_id: str = Field(min_length=36, max_length=36)


class ReviewListParams(PaginationParams):
    pass
