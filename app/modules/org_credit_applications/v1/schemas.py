from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import ConfigDict, EmailStr, Field, model_validator

from app.common.schemas import BaseSchema, CurrencyAmount, IDSchema, PaginationParams, SuccessResponse, TimestampSchema, UserSchema
from app.modules.org_credit.v1.schemas import CreditReportResponse
from app.modules.org_credit_applications.enums import (
    BankAccountType,
    CreditApplicationLifecycleState,
    CreditApplicationStatus,
    EmployeeRange,
    Industry,
    OrgCreditLimitIncreaseRequestStatus,
    RejectionCategory,
    RelationshipDuration,
    ReviewFrequency,
    TradeReferenceVerificationStatus,
)

DraftListActor = Literal["ADMIN", "CLIENT"]


class UserRef(BaseSchema):
    id: str
    first_name: str | None = None
    last_name: str | None = None


class DraftCreatorRef(BaseSchema):
    id: str
    email: str | None = None


class TradeReferenceInput(BaseSchema):
    company_name: str | None = Field(default=None, max_length=255)
    contact_person: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=40)
    contact_email: EmailStr | None = None
    account_number_reference: str | None = Field(default=None, max_length=100)
    credit_limit_with_reference: CurrencyAmount | None = None
    relationship_duration: RelationshipDuration | None = None


class TradeReferenceResponse(BaseSchema):
    id: str
    ref_index: int
    company_name: str | None
    contact_person: str | None
    contact_phone: str | None
    contact_email: str | None
    account_number_reference: str | None
    credit_limit_with_reference: Decimal | None
    relationship_duration: RelationshipDuration | None
    verification_status: TradeReferenceVerificationStatus
    verified_at: datetime | None
    verified_by_user_id: str | None
    created_at: datetime
    updated_at: datetime


class TradeReferenceSubmissionValidator(BaseSchema):
    company_name: str = Field(min_length=1, max_length=255)
    contact_person: str = Field(min_length=1, max_length=255)
    contact_phone: str = Field(min_length=1, max_length=40)
    contact_email: EmailStr
    relationship_duration: RelationshipDuration


class CreditApplicationSubmissionValidator(BaseSchema):
    company_registration_number: str = Field(min_length=1)
    years_trading: int = Field(ge=0)
    annual_turnover: Decimal = Field(gt=0)
    bank_name: str = Field(min_length=1)
    bank_sort_code: str = Field(min_length=1)
    bank_account_number_last4: str = Field(min_length=1)
    requested_credit_limit: Decimal = Field(gt=0)
    requested_payment_terms_days: int = Field(ge=1, le=365)
    expected_monthly_spend: Decimal = Field(gt=0)
    director_signatory_name: str = Field(min_length=1)
    director_signatory_position: str = Field(min_length=1)
    consent_credit_check: Literal[True]
    consent_terms_and_conditions: Literal[True]
    consent_data_processing: Literal[True]
    trade_references: list[TradeReferenceSubmissionValidator] = Field(min_length=2)


class CreateCreditApplicationRequest(BaseSchema):
    company_registration_number: str | None = Field(default=None, max_length=32)
    vat_registration_number: str | None = Field(default=None, max_length=32)
    industry: Industry | None = None
    number_of_employees: EmployeeRange | None = None
    date_of_incorporation: date | None = None
    years_trading: int | None = Field(default=None, ge=0, le=200)
    annual_turnover: CurrencyAmount | None = None
    net_profit: CurrencyAmount | None = None

    trade_references: list[TradeReferenceInput] | None = None

    bank_name: str | None = Field(default=None, max_length=255)
    bank_sort_code: str | None = Field(default=None, max_length=12)
    bank_account_number_last4: str | None = Field(default=None, max_length=10)
    bank_account_type: BankAccountType | None = None

    requested_credit_limit: CurrencyAmount | None = None
    requested_payment_terms_days: int | None = Field(default=None, ge=1, le=365)
    expected_monthly_spend: CurrencyAmount | None = None
    seasonal_peaks: list[str] | None = Field(default=None, max_length=12)
    justification: str | None = Field(default=None, max_length=2000)

    director_signatory_name: str | None = Field(default=None, max_length=255)
    director_signatory_position: str | None = Field(default=None, max_length=120)
    declaration_date: date | None = None
    consent_credit_check: bool = False
    consent_terms_and_conditions: bool = False
    consent_data_processing: bool = False


class SaveDraftRequest(BaseSchema):
    company_registration_number: str | None = Field(default=None, max_length=32)
    vat_registration_number: str | None = Field(default=None, max_length=32)
    industry: Industry | None = None
    number_of_employees: EmployeeRange | None = None
    date_of_incorporation: date | None = None
    years_trading: int | None = Field(default=None, ge=0, le=200)
    annual_turnover: CurrencyAmount | None = None
    net_profit: CurrencyAmount | None = None

    trade_references: list[TradeReferenceInput] | None = None

    bank_name: str | None = Field(default=None, max_length=255)
    bank_sort_code: str | None = Field(default=None, max_length=12)
    bank_account_number_last4: str | None = Field(default=None, max_length=10)
    bank_account_type: BankAccountType | None = None

    requested_credit_limit: CurrencyAmount | None = None
    requested_payment_terms_days: int | None = Field(default=None, ge=1, le=365)
    expected_monthly_spend: CurrencyAmount | None = None
    seasonal_peaks: list[str] | None = Field(default=None, max_length=12)
    justification: str | None = Field(default=None, max_length=2000)

    director_signatory_name: str | None = Field(default=None, max_length=255)
    director_signatory_position: str | None = Field(default=None, max_length=120)
    declaration_date: date | None = None
    consent_credit_check: bool | None = None
    consent_terms_and_conditions: bool | None = None
    consent_data_processing: bool | None = None


class UpdateDraftRequest(SaveDraftRequest):
    pass


class EditCompanyFinancialInfoRequest(BaseSchema):
    company_registration_number: str | None = Field(default=None, max_length=32)
    vat_registration_number: str | None = Field(default=None, max_length=32)
    industry: Industry | None = None
    number_of_employees: EmployeeRange | None = None
    date_of_incorporation: date | None = None
    years_trading: int | None = Field(default=None, ge=0, le=200)
    annual_turnover: CurrencyAmount | None = None
    net_profit: CurrencyAmount | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> EditCompanyFinancialInfoRequest:
        if not self.model_dump(exclude_unset=True):
            raise ValueError("At least one field must be provided")
        return self


class PatchBankReferenceBody(BaseSchema):
    bank_name: str | None = Field(default=None, max_length=255)
    bank_sort_code: str | None = Field(default=None, max_length=12)
    bank_account_number_last4: str | None = Field(default=None, max_length=10)
    bank_account_type: BankAccountType | None = None


class EditRequestedCreditTermsRequest(BaseSchema):
    requested_credit_limit: CurrencyAmount | None = None
    requested_payment_terms_days: int | None = Field(default=None, ge=1, le=365)
    expected_monthly_spend: CurrencyAmount | None = None
    seasonal_peaks: list[str] | None = Field(default=None, max_length=12)
    justification: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def at_least_one_field(self) -> EditRequestedCreditTermsRequest:
        if not self.model_dump(exclude_unset=True):
            raise ValueError("At least one field must be provided")
        return self


class EditDeclarationsRequest(BaseSchema):
    director_signatory_name: str | None = Field(default=None, max_length=255)
    director_signatory_position: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def at_least_one_field(self) -> EditDeclarationsRequest:
        if not self.model_dump(exclude_unset=True):
            raise ValueError("At least one field must be provided")
        return self


class EditTradeReferenceRequest(BaseSchema):
    company_name: str | None = Field(default=None, max_length=255)
    contact_person: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=40)
    contact_email: EmailStr | None = None
    account_number_reference: str | None = Field(default=None, max_length=100)
    credit_limit_with_reference: CurrencyAmount | None = None
    relationship_duration: RelationshipDuration | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> EditTradeReferenceRequest:
        if not self.model_dump(exclude_unset=True):
            raise ValueError("At least one field must be provided")
        return self


class AddTradeReferenceRequest(BaseSchema):
    company_name: str | None = Field(default=None, max_length=255)
    contact_person: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=40)
    contact_email: EmailStr | None = None
    account_number_reference: str | None = Field(default=None, max_length=100)
    credit_limit_with_reference: CurrencyAmount | None = None
    relationship_duration: RelationshipDuration | None = None


class VerifyTradeReferenceRequest(BaseSchema):
    verification_status: TradeReferenceVerificationStatus


class CreditApplicationListParams(PaginationParams):
    status: CreditApplicationStatus | None = Field(default=None)
    search: str | None = Field(default=None, max_length=200)


class CreditApplicationDraftListParams(PaginationParams):
    pass


class BankReferenceLetterResponse(BaseSchema):
    id: str
    url: str | None
    filename: str | None


class BankReferenceResponse(BaseSchema):
    bank_name: str | None
    bank_sort_code: str | None
    bank_account_number_last4: str | None
    bank_account_type: BankAccountType | None
    reference_letter: BankReferenceLetterResponse | None = None


class CreditApplicationDraftApplicationView(BaseSchema):
    company_registration_number: str | None
    vat_registration_number: str | None
    industry: Industry | None
    number_of_employees: EmployeeRange | None
    date_of_incorporation: date | None
    years_trading: int | None
    annual_turnover: Decimal | None
    net_profit: Decimal | None

    trade_references: list[TradeReferenceResponse] = Field(default_factory=list)
    bank_reference: BankReferenceResponse | None = None

    requested_credit_limit: Decimal | None
    requested_payment_terms_days: int | None
    expected_monthly_spend: Decimal | None
    seasonal_peaks: list[str] | None
    justification: str | None

    director_signatory_name: str | None
    director_signatory_position: str | None
    declaration_date: date | None
    consent_credit_check: bool
    consent_terms_and_conditions: bool
    consent_data_processing: bool


class CreditApplicationCooldownSnippet(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    active: bool
    summary: str | None


class CreditApplicationDetailView(IDSchema, TimestampSchema, BaseSchema):
    organization_id: str
    application_number: str | None
    state: CreditApplicationLifecycleState
    status: CreditApplicationStatus

    company_registration_number: str | None
    vat_registration_number: str | None
    industry: Industry | None
    number_of_employees: EmployeeRange | None
    date_of_incorporation: date | None
    years_trading: int | None
    annual_turnover: Decimal | None
    net_profit: Decimal | None

    trade_references: list[TradeReferenceResponse] = Field(default_factory=list)
    bank_reference: BankReferenceResponse | None = None

    requested_credit_limit: Decimal | None
    requested_payment_terms_days: int | None
    expected_monthly_spend: Decimal | None
    seasonal_peaks: list[str] | None
    justification: str | None

    director_signatory_name: str | None
    director_signatory_position: str | None
    declaration_date: date | None
    consent_credit_check: bool
    consent_terms_and_conditions: bool
    consent_data_processing: bool

    submitted_by: UserRef | None = None
    assigned_reviewer: UserRef | None = None

    submitted_at: datetime | None
    reviewer_assigned_at: datetime | None
    references_verified_at: datetime | None
    decided_at: datetime | None

    approved_at: datetime | None
    approved_by: UserRef | None
    rejected_at: datetime | None
    rejected_by: UserRef | None
    cancelled_at: datetime | None
    cancelled_by: UserRef | None
    withdrawn_at: datetime | None
    withdrawn_by: UserRef | None

    approved_credit_limit: Decimal | None
    approved_payment_terms_days: int | None
    review_frequency: ReviewFrequency | None
    approval_notes: str | None
    rejection_category: RejectionCategory | None
    rejection_reason: str | None
    cancellation_reason: str | None
    internal_notes: str | None
    deleted_at: datetime | None

    credit_report: CreditReportResponse | None = None


class CreateCreditLimitIncreaseRequestBody(BaseSchema):
    requested_credit_limit: CurrencyAmount = Field(gt=0)
    reason: str = Field(min_length=1, max_length=10000)


class ApproveCreditLimitIncreaseRequestBody(BaseSchema):
    approved_credit_limit: CurrencyAmount = Field(gt=0)


class CreditLimitIncreaseRequestListParams(PaginationParams):
    pass


class CreditLimitIncreaseRequestResponse(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    id: str
    previous_limit: Decimal | None
    requested_limit: Decimal
    approved_limit: Decimal | None
    reason: str
    status: OrgCreditLimitIncreaseRequestStatus
    requested_by: UserSchema
    reviewed_by: UserSchema | None = None
    reviewed_at: datetime | None = None
    created_at: datetime


class CreditApplicationCurrentDetailView(CreditApplicationDetailView):
    cooldown: CreditApplicationCooldownSnippet | None = None
    pending_credit_limit_increase_request: CreditLimitIncreaseRequestResponse | None = None


class CreditApplicationListItem(BaseSchema):
    id: str
    application_number: str | None
    status: CreditApplicationStatus
    submitted_at: datetime | None
    requested_credit_limit: Decimal | None
    assigned_reviewer: UserRef | None = None


class CreditApplicationDraftListItem(BaseSchema):
    id: str
    draft_number: str
    created_at: datetime
    actor: DraftListActor | None = None
    created_by: DraftCreatorRef | None = None


class CreditApplicationDraftData(BaseSchema):
    id: str
    draft_number: str
    created_at: datetime


class CreditApplicationDraftDetail(CreditApplicationDraftData):
    application: CreditApplicationDraftApplicationView


class CreditApplicationCreatedResponse(BaseSchema):
    id: str
    application_number: str | None


class FileUploadFailure(BaseSchema):
    index: int
    filename: str
    reason: str


class CreditApplicationDraftSaveResponse(SuccessResponse[CreditApplicationDraftData]):
    model_config = ConfigDict(extra="forbid")

    failed_documents: list[FileUploadFailure] = Field(default_factory=list)


class CreditApplicationCreatedWithUploadsResponse(SuccessResponse[CreditApplicationCreatedResponse]):
    model_config = ConfigDict(extra="forbid")

    failed_documents: list[FileUploadFailure] = Field(default_factory=list)


class MessageWithUploadsResponse(BaseSchema):
    model_config = ConfigDict(extra="forbid")

    success: Literal[True] = True
    message: str
    failed_documents: list[FileUploadFailure] = Field(default_factory=list)


class AssignReviewerBody(BaseSchema):
    reviewer_user_id: str


class ApproveCreditApplicationBody(BaseSchema):
    approved_credit_limit: CurrencyAmount = Field(gt=0)
    approved_payment_terms_days: int = Field(ge=1, le=365)
    review_frequency: ReviewFrequency | None = None
    approval_notes: str | None = Field(default=None, max_length=20000)


class RejectCreditApplicationBody(BaseSchema):
    rejection_category: RejectionCategory
    detailed_reason: str = Field(min_length=1, max_length=10000)


class CancelApplicationBody(BaseSchema):
    reason: str = Field(min_length=1, max_length=10000)
