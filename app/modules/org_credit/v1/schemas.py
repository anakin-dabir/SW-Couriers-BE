from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.common.schemas import BaseSchema, PaginationParams, UserSchema
from app.modules.audit.enums import AuditEventType
from app.modules.org_credit.enums import (
    CloseAccountReason,
    HoldReasonCategory,
    InternalCreditScoreBand,
    OrgCreditInvestigationStatus,
)

CreditActivityUserType = Literal["Admin", "Client", "System"]
CreditActivitySeverity = Literal["INFO", "NOTICE", "WARNING", "CRITICAL"]


class CreditAccountSummarySchema(BaseSchema):
    """Lean credit account projection returned everywhere the account is referenced."""

    id: str
    organization_id: str
    status: str
    status_reason: str | None = None
    action_by_user_id: str | None = None
    credit_limit: str | None
    used_credit: str
    available_credit: str
    review_frequency: str | None
    review_risk_level: str | None = None
    last_status_change_at: str | None
    credit_facility_start_date: str | None
    credit_facility_end_date: str | None
    credit_limit_updated_at: str | None
    pending_credit_limit: str | None
    pending_credit_limit_effective_from: str | None
    payment_terms_days: int | None
    pending_payment_terms_days: int | None
    pending_payment_terms_effective_from: str | None
    payment_terms_updated_at: str | None
    payment_terms_effective_from: str | None
    created_at: str
    updated_at: str


class CreditReportSummarySchema(BaseSchema):
    connect_id: str | None
    credit_score: int | None
    credit_score_max: int | None
    credit_rating: str | None
    company_name: str | None
    last_checked_at: str | None


class CreditConfigSummarySchema(BaseSchema):
    approved_credit_limit: str | None
    credit_utilization_warning_pct: int | None
    credit_clearance_period_days: int | None
    allow_bookings_beyond_limit: bool


class CreditOverviewCreditStatusSchema(BaseSchema):
    status: str | None
    last_changed_at: str | None
    reason: str | None
    action_by: UserSchema | None


class CreditOverviewCreditLimitSchema(BaseSchema):
    amount: str | None
    last_adjusted_at: str | None


class CreditOverviewCreditTermsSchema(BaseSchema):
    payment_terms_days: int | None
    terms_label: str | None


class CreditOverviewNextReviewSchema(BaseSchema):
    due_date: str | None
    days_remaining: int | None


class CreditOverviewOutstandingBalanceSchema(BaseSchema):
    total: str | None
    as_of: str | None
    current: str | None
    unpaid_invoice_count: int | None
    overdue_portion: str | None


class CreditOverviewOverdueSchema(BaseSchema):
    total: str | None
    overdue_invoice_count: int | None
    oldest_overdue_days: int | None


class CreditOverviewNextInvoiceSchema(BaseSchema):
    due_date: str | None
    days_until_due: int | None


class CreditOverviewInternalScoreSchema(BaseSchema):
    score: int | None
    label: InternalCreditScoreBand | None
    last_recalculated_at: str | None


class CreditAccountOverviewResponse(BaseSchema):
    """Minimal credit-account snapshot for the order-creation UI.

    Returned by ``GET /organizations/{org_id}/credit/account-overview``. The
    route raises 404 when no account exists so the FE can show a configure-
    credit-account banner. ``credit_limit_used_percent`` is rounded for the
    progress bar (caller still gets exact figures via the string amounts).
    """

    status: str
    credit_limit: str
    outstanding_balance: str
    available_credit: str
    credit_limit_used_percent: float


class CreditOverviewResponse(BaseSchema):
    account: CreditAccountSummarySchema | None
    utilization_percent: float | None
    available_credit: str | None
    credit_status: CreditOverviewCreditStatusSchema | None
    credit_limit: CreditOverviewCreditLimitSchema | None
    credit_terms: CreditOverviewCreditTermsSchema | None
    next_review: CreditOverviewNextReviewSchema | None
    outstanding_balance: CreditOverviewOutstandingBalanceSchema | None
    overdue: CreditOverviewOverdueSchema | None
    next_invoice: CreditOverviewNextInvoiceSchema | None
    internal_credit_score: CreditOverviewInternalScoreSchema | None
    report_summary: CreditReportSummarySchema | None
    config_summary: CreditConfigSummarySchema | None
    credit_facility_end_date: str | None
    risk_flags: list[str]


class CreditOverviewTrendQuery(BaseSchema):
    year: int = Field(ge=2020, le=2030)
    month: int | None = Field(default=None, ge=1, le=12)
    granularity: str = Field(default="monthly")

    @field_validator("granularity")
    @classmethod
    def validate_granularity(cls, v: str) -> str:
        allowed = {"weekly", "monthly", "yearly", "daily"}
        if v not in allowed:
            raise ValueError(f"granularity must be one of: {', '.join(sorted(allowed))}")
        return v

    @model_validator(mode="after")
    def daily_requires_month(self) -> CreditOverviewTrendQuery:
        if self.granularity == "daily" and self.month is None:
            raise ValueError("month is required when granularity is daily")
        return self


class PlaceHoldRequest(BaseSchema):
    hold_reason_category: HoldReasonCategory
    detailed_reason: str | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def require_detailed_reason_for_other(self) -> PlaceHoldRequest:
        if self.hold_reason_category == HoldReasonCategory.OTHER and not self.detailed_reason:
            raise ValueError("detailed_reason is required when hold_reason_category is OTHER")
        return self


class ReleaseHoldRequest(BaseSchema):
    memo: str | None = Field(default=None, max_length=2000)


class SuspendAccountRequest(BaseSchema):
    reason: str = Field(min_length=1, max_length=10000)
    trigger_payment_acceleration: bool = False


class ReactivateAccountRequest(BaseSchema):
    memo: str | None = Field(default=None, max_length=2000)


class CloseAccountRequest(BaseSchema):
    reason_category: CloseAccountReason
    detailed_reason: str | None = Field(default=None, max_length=10000)
    confirmation_text: str = Field(min_length=1, max_length=10)

    @field_validator("confirmation_text")
    @classmethod
    def validate_confirmation(cls, v: str) -> str:
        if v.strip().upper() != "CLOSE":
            raise ValueError("confirmation_text must be 'CLOSE'")
        return v


class CreditAccountMutationResponse(CreditOverviewCreditStatusSchema):
    pass


class CreditActivityEntryResponse(BaseSchema):
    id: str
    event_type: AuditEventType
    event_label: str
    description: str | None
    user_type: CreditActivityUserType
    severity: CreditActivitySeverity
    acted_by: str | None
    acted_by_email: str | None
    timestamp: datetime
    audit_ref: str | None = None
    entity_ref: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    ip_address: str | None = None
    browser: str | None = None
    device: str | None = None
    os: str | None = None


class CreditActivityListParams(PaginationParams):
    search: str | None = Field(
        default=None, max_length=200,
        description="Free text — matches event type, action, reason, actor email/name.",
    )
    event_type: list[AuditEventType] | None = Field(
        default=None, description="Limit to one or more event types.",
    )
    user_type: list[CreditActivityUserType] | None = Field(
        default=None, description="Filter by actor bucket: Admin, Client, or System.",
    )
    severity: list[CreditActivitySeverity] | None = Field(
        default=None, description="Limit to one or more severities.",
    )
    from_date: datetime | None = Field(
        default=None, description="Start of the timestamp window (inclusive, ISO-8601).",
    )
    to_date: datetime | None = Field(
        default=None, description="End of the timestamp window (inclusive, ISO-8601).",
    )

    @model_validator(mode="after")
    def _normalise_lists_and_window(self) -> CreditActivityListParams:
        if self.event_type is not None and len(self.event_type) == 0:
            self.event_type = None
        if self.user_type is not None and len(self.user_type) == 0:
            self.user_type = None
        if self.severity is not None and len(self.severity) == 0:
            self.severity = None
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from_date must be on or before to_date")
        return self


class StatusHistoryEntryResponse(BaseSchema):
    id: str
    from_status: str | None
    to_status: str
    reason: str | None
    duration: str
    created_at: str
    action_by: UserSchema | None


class StatusHistoryListParams(PaginationParams):
    pass


RiskIndicatorSeverity = Literal["OK", "WARNING", "ALERT"]


class CreditScoreSection(BaseSchema):
    """The "Score Calculated" panel on the credit report UI."""

    recommended_credit_limit: Decimal | None = None
    recommended_credit_limit_currency: str | None = None
    credit_rating: str | None = None
    credit_score: int | None = None
    credit_score_max: int | None = None
    rating_description: str | None = None
    previous_credit_rating: str | None = None
    previous_rating_changed_at: date | None = None
    risk_band: str | None = None
    probability_of_default_12m: Decimal | None = None
    assessment_commentary: str | None = None


class CreditRiskIndicator(BaseSchema):
    """One entry in the "Risk Indicators" panel.

    ``severity`` drives the UI treatment (OK = green tick, WARNING = amber,
    ALERT = red). ``description`` is always present — for the "no issues"
    case it reads like "No active insolvency proceedings". ``details``
    carries the structured per-item records when the indicator is firing.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    severity: RiskIndicatorSeverity
    description: str
    details: list[dict[str, Any]] = Field(default_factory=list)


class CreditCompanyInformation(BaseSchema):
    """The "Company Information" panel on the credit report UI."""

    trading_name: str | None = None
    legal_entity_name: str | None = None
    company_registration_number: str | None = None
    industry_code: str | None = None
    industry_description: str | None = None
    date_of_incorporation: date | None = None
    vat_number: str | None = None
    contact_number: str | None = None
    registered_address: str | None = None
    country: str | None = None
    company_status: str | None = None
    latest_turnover: Decimal | None = None
    latest_turnover_currency: str | None = None


class CreditDirectorInfo(BaseSchema):
    """One row in the "Directors Information" panel.

    ``flags`` holds the human-readable negative flags attached to this
    specific director (e.g. "Historical linkage to dissolved entity").
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    role: str | None = None
    appointed_on: date | None = None
    date_of_birth: date | None = None
    flags: list[str] = Field(default_factory=list)


class CreditReportResponse(BaseSchema):
    """Creditsafe credit report snapshot stored in org_credit_reports.

    Response groups mirror the UI panels: score, risk indicators,
    company information, directors, payment behaviour.
    """

    id: str
    connect_id: str | None = None
    score: CreditScoreSection
    risk_indicators: list[CreditRiskIndicator] = Field(default_factory=list)
    company_information: CreditCompanyInformation
    directors: list[CreditDirectorInfo] = Field(default_factory=list)
    payment_behaviour: str | None = None
    last_checked_at: datetime | None = None
    checked_by_user_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_report(cls, report: Any) -> CreditReportResponse:
        """Build a response from an ``OrgCreditReport`` ORM instance.

        Legacy rows may have ``directors`` stored as a dict (e.g. the
        raw Creditsafe ``{"currentDirectors": [...]}`` wrapper) and
        ``risk_indicators`` may be missing or malformed. These are
        normalised to empty lists here so the response stays valid.
        """
        return cls(
            id=report.id,
            connect_id=report.connect_id,
            score=CreditScoreSection(
                recommended_credit_limit=report.recommended_credit_limit,
                recommended_credit_limit_currency=report.recommended_credit_limit_currency,
                credit_rating=report.credit_rating,
                credit_score=report.credit_score,
                credit_score_max=report.credit_score_max,
                rating_description=report.credit_rating_description,
                previous_credit_rating=report.previous_credit_rating,
                previous_rating_changed_at=report.previous_rating_changed_at,
                risk_band=report.risk_band,
                probability_of_default_12m=report.probability_of_default_12m,
                assessment_commentary=report.assessment_commentary,
            ),
            risk_indicators=_safe_risk_indicators(report.risk_indicators),
            company_information=CreditCompanyInformation(
                trading_name=report.company_name,
                legal_entity_name=report.legal_entity_name,
                company_registration_number=report.company_registration_number,
                industry_code=report.industry_code,
                industry_description=report.industry_description,
                date_of_incorporation=report.date_of_incorporation,
                vat_number=report.vat_number,
                contact_number=report.contact_number,
                registered_address=report.registered_address,
                country=report.country,
                company_status=report.company_status,
                latest_turnover=report.latest_turnover,
                latest_turnover_currency=report.latest_turnover_currency,
            ),
            directors=_safe_directors(report.directors),
            payment_behaviour=report.payment_behaviour_description,
            last_checked_at=report.last_checked_at,
            checked_by_user_id=report.checked_by_user_id,
            created_at=report.created_at,
            updated_at=report.updated_at,
        )


def _safe_directors(raw: Any) -> list[CreditDirectorInfo]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = raw.get("currentDirectors") or raw.get("directors") or []
    if not isinstance(raw, list):
        return []
    results: list[CreditDirectorInfo] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            results.append(CreditDirectorInfo.model_validate(entry))
        except Exception:
            continue
    return results


def _safe_risk_indicators(raw: Any) -> list[CreditRiskIndicator]:
    if not isinstance(raw, list):
        return []
    results: list[CreditRiskIndicator] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            results.append(CreditRiskIndicator.model_validate(entry))
        except Exception:
            continue
    return results


class CreditInvestigationResponse(BaseSchema):
    """A fresh investigation order placed with Creditsafe when the standard search returns no match."""

    id: str
    status: OrgCreditInvestigationStatus
    provider_reference: str | None
    connect_id: str | None
    reg_no: str | None
    company_name: str | None
    country: str | None
    requested_at: datetime
    completed_at: datetime | None
    failure_reason: str | None


CreditCheckOutcome = Literal["COMPLETED", "INVESTIGATION_PROGRESS", "FAILED"]


class CreditCheckResult(BaseSchema):
    """Unified envelope for run / refresh credit check endpoints.

    Exactly one of `report` or `investigation` is populated based on outcome.
    """

    outcome: CreditCheckOutcome
    report: CreditReportResponse | None = None
    investigation: CreditInvestigationResponse | None = None
    message: str | None = None
