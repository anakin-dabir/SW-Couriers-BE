from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import Sequence

from app.common.enums.sequence import SequentialPrefix
from app.common.models import AppendOnlyModel, Base, BaseModel, BaseModelNoVersion
from app.modules.org_credit_applications.enums import (
    AttachmentType,
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

if TYPE_CHECKING:
    from app.modules.user.models import User

credit_app_seq = Sequence("credit_app_seq", metadata=Base.metadata)
credit_app_draft_seq = Sequence("credit_app_draft_seq", metadata=Base.metadata)


class OrgCreditApplication(BaseModel):
    __tablename__ = "org_credit_applications"

    application_number: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
        unique=True,
        index=True,
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_credit_app_organization_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[CreditApplicationStatus] = mapped_column(
        sa.Enum(CreditApplicationStatus, name="creditapplicationstatus", native_enum=False),
        nullable=False,
        default=CreditApplicationStatus.SUBMITTED,
    )
    state: Mapped[CreditApplicationLifecycleState] = mapped_column(
        sa.Enum(CreditApplicationLifecycleState, name="creditapplicationlifecyclestate", native_enum=False),
        nullable=False,
        default=CreditApplicationLifecycleState.DRAFT,
        index=True,
    )

    submitted_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_credit_app_submitted_by_user_id", ondelete="SET NULL"),
        nullable=True,
    )

    company_registration_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vat_registration_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    industry: Mapped[Industry | None] = mapped_column(
        sa.Enum(Industry, name="creditappindustry", native_enum=False),
        nullable=True,
    )
    number_of_employees: Mapped[EmployeeRange | None] = mapped_column(
        sa.Enum(
            EmployeeRange,
            name="creditappemployeerange",
            native_enum=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
    date_of_incorporation: Mapped[date | None] = mapped_column(Date, nullable=True)
    years_trading: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annual_turnover: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bank_sort_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    bank_account_number_last4: Mapped[str | None] = mapped_column(String(10), nullable=True)
    bank_account_type: Mapped[BankAccountType | None] = mapped_column(
        sa.Enum(BankAccountType, name="creditappbankaccounttype", native_enum=False),
        nullable=True,
    )
    requested_credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    requested_payment_terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_monthly_spend: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    seasonal_peaks: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)

    director_signatory_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    director_signatory_position: Mapped[str | None] = mapped_column(String(120), nullable=True)
    declaration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    consent_credit_check: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consent_terms_and_conditions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consent_data_processing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    assigned_reviewer_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_credit_app_assigned_reviewer_user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    references_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    credit_check_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    approved_credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    approved_payment_terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_frequency: Mapped[ReviewFrequency | None] = mapped_column(
        sa.Enum(ReviewFrequency, name="creditappreviewfrequency", native_enum=False),
        nullable=True,
    )
    approval_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_category: Mapped[RejectionCategory | None] = mapped_column(
        sa.Enum(RejectionCategory, name="creditapprejectioncategory", native_enum=False),
        nullable=True,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_credit_app_approved_by_user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_credit_app_rejected_by_user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_credit_app_cancelled_by_user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_credit_app_withdrawn_by_user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    trade_references: Mapped[list[OrgCreditApplicationTradeReference]] = relationship(
        "OrgCreditApplicationTradeReference",
        back_populates="application",
        lazy="raise",
        order_by="OrgCreditApplicationTradeReference.ref_index",
        cascade="all, delete-orphan",
    )
    reviewer: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[assigned_reviewer_user_id],
        lazy="raise",
    )
    submitted_by_user: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[submitted_by_user_id],
        lazy="raise",
    )
    approved_by_user: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[approved_by_user_id],
        lazy="raise",
    )
    rejected_by_user: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[rejected_by_user_id],
        lazy="raise",
    )
    cancelled_by_user: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[cancelled_by_user_id],
        lazy="raise",
    )
    withdrawn_by_user: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[withdrawn_by_user_id],
        lazy="raise",
    )


class OrgCreditApplicationTradeReference(BaseModelNoVersion):
    __tablename__ = "org_credit_application_trade_references"

    __table_args__ = (Index("ix_credit_app_trade_ref_app_idx", "application_id", "ref_index"),)

    application_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_credit_applications.id", name="fk_trade_ref_application_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ref_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_person: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    account_number_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    credit_limit_with_reference: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    relationship_duration: Mapped[RelationshipDuration | None] = mapped_column(
        sa.Enum(RelationshipDuration, name="creditapprelationshipduration", native_enum=False),
        nullable=True,
    )

    verification_status: Mapped[TradeReferenceVerificationStatus] = mapped_column(
        sa.Enum(TradeReferenceVerificationStatus, name="tradereferenceverificationstatus", native_enum=False),
        nullable=False,
        default=TradeReferenceVerificationStatus.PENDING,
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_trade_ref_verified_by_user_id", ondelete="SET NULL"),
        nullable=True,
    )

    application: Mapped[OrgCreditApplication] = relationship(
        "OrgCreditApplication",
        back_populates="trade_references",
        lazy="raise",
    )


class OrgCreditApplicationDraft(BaseModelNoVersion):
    __tablename__ = "org_credit_application_drafts"

    draft_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'{SequentialPrefix.CREDIT_APP_DRAFT}-' || lpad(nextval('{credit_app_draft_seq.name}')::text, 3, '0')"),
    )
    application_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_credit_applications.id", name="fk_draft_application_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    created_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_draft_created_by_id", ondelete="SET NULL"),
        nullable=True,
    )
    published_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_draft_published_by_id", ondelete="SET NULL"),
        nullable=True,
    )

    application: Mapped[OrgCreditApplication] = relationship("OrgCreditApplication", lazy="raise")
    created_by_user: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[created_by_id],
        lazy="raise",
    )


class OrgCreditApplicationAttachment(AppendOnlyModel):
    __tablename__ = "org_credit_application_attachments"

    application_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_credit_applications.id", name="fk_credit_app_attachment_app_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_credit_app_attachment_org_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attachment_type: Mapped[AttachmentType] = mapped_column(
        sa.Enum(AttachmentType, name="creditappattachmenttype", native_enum=False),
        nullable=False,
    )
    r2_key: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_credit_app_attachment_uploaded_by", ondelete="SET NULL"),
        nullable=True,
    )
    document_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)


class OrgCreditLimitIncreaseRequest(AppendOnlyModel):
    __tablename__ = "org_credit_limit_increase_requests"
    __table_args__ = (
        Index(
            "uq_oclis_one_pending_per_org",
            "organization_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index("ix_oclis_org_created", "organization_id", "created_at"),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_oclis_org_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    previous_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    requested_limit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    approved_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[OrgCreditLimitIncreaseRequestStatus] = mapped_column(
        sa.Enum(OrgCreditLimitIncreaseRequestStatus, name="oclis_status", native_enum=False),
        nullable=False,
        server_default=OrgCreditLimitIncreaseRequestStatus.PENDING.value,
    )
    requested_by_user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_oclis_requested_by", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    reviewed_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_oclis_reviewed_by", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requested_by_user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[requested_by_user_id],
        lazy="raise",
    )
    reviewed_by_user: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[reviewed_by_user_id],
        lazy="raise",
    )
