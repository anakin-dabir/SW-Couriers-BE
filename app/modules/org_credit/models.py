from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AppendOnlyModel, BaseModelNoVersion
from app.modules.org_credit.enums import (
    OrgCreditAccountStatus,
    OrgCreditAdjustmentReason,
    OrgCreditInvestigationStatus,
    OrgCreditLedgerMovementType,
    OrgCreditLedgerSourceType,
    OrgCreditReviewFrequency,
)
from app.modules.org_credit_reviews.enums import CreditReviewReminderPeriod, CreditReviewRiskLevel

if TYPE_CHECKING:
    from app.modules.user.models import User

_org_credit_account_status_sa = sa.Enum(
    OrgCreditAccountStatus, name="orgcreditaccountstatus", native_enum=False,
)


class OrgCreditReport(BaseModelNoVersion):
    __tablename__ = "org_credit_reports"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_credit_report_organization_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    connect_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    credit_score: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    credit_score_max: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    credit_rating: Mapped[str | None] = mapped_column(String(10), nullable=True)
    credit_rating_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recommended_credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    recommended_credit_limit_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    previous_credit_rating: Mapped[str | None] = mapped_column(String(10), nullable=True)
    previous_rating_changed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    risk_band: Mapped[str | None] = mapped_column(String(50), nullable=True)
    probability_of_default_12m: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    assessment_commentary: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    legal_entity_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    company_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    company_registration_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    date_of_incorporation: Mapped[date | None] = mapped_column(Date, nullable=True)
    country: Mapped[str | None] = mapped_column(String(10), nullable=True)
    latest_turnover: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    latest_turnover_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    registered_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    industry_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vat_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    contact_number: Mapped[str | None] = mapped_column(String(40), nullable=True)

    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checked_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_credit_report_checked_by_user_id", ondelete="SET NULL"),
        nullable=True,
    )

    directors: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    risk_indicators: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    payment_behaviour_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class OrgCreditAccount(BaseModelNoVersion):
    __tablename__ = "org_credit_accounts"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_org_credit_account_organization_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    status: Mapped[OrgCreditAccountStatus] = mapped_column(
        _org_credit_account_status_sa,
        nullable=False,
        server_default=OrgCreditAccountStatus.ACTIVE.value,
    )
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_org_credit_account_action_by_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    action_by_user: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[action_by_user_id],
        lazy="raise",
    )
    last_status_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    credit_limit_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    pending_credit_limit_effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)

    payment_terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_payment_terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_payment_terms_effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_terms_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_terms_effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)

    used_credit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, server_default="0")

    review_frequency: Mapped[OrgCreditReviewFrequency | None] = mapped_column(
        sa.Enum(OrgCreditReviewFrequency, name="orgcreditreviewfrequency", native_enum=False),
        nullable=True,
    )
    next_review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    review_reminder_period: Mapped[CreditReviewReminderPeriod | None] = mapped_column(
        sa.Enum(CreditReviewReminderPeriod, name="creditreviewreminderperiod", native_enum=False),
        nullable=True,
    )
    assigned_reviewer_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_org_credit_account_reviewer_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    review_risk_level: Mapped[CreditReviewRiskLevel | None] = mapped_column(
        sa.Enum(CreditReviewRiskLevel, name="creditreviewrisklevel", native_enum=False, create_constraint=False),
        nullable=True,
    )

    hold_threshold_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)

    credit_facility_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    credit_facility_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    ledger_entries: Mapped[list["OrgCreditLedgerEntry"]] = relationship(
        "OrgCreditLedgerEntry",
        back_populates="account",
        lazy="raise",
    )


class OrgCreditLedgerEntry(AppendOnlyModel):
    __tablename__ = "org_credit_ledger_entries"

    __table_args__ = (
        sa.Index("ix_org_credit_ledger_org_created", "organization_id", "created_at"),
        sa.Index(
            "uq_org_credit_ledger_idempotency",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_org_credit_ledger_organization_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_credit_accounts.id", name="fk_org_credit_ledger_account_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    movement_type: Mapped[OrgCreditLedgerMovementType] = mapped_column(
        sa.Enum(OrgCreditLedgerMovementType, name="orgcreditledgermovementtype", native_enum=False),
        nullable=False,
    )

    source_type: Mapped[OrgCreditLedgerSourceType | None] = mapped_column(
        sa.Enum(OrgCreditLedgerSourceType, name="orgcreditledgersourcetype", native_enum=False),
        nullable=True,
    )
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    used_credit_after: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    available_credit_after: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    credit_limit_after: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    adjustment_reason: Mapped[OrgCreditAdjustmentReason | None] = mapped_column(
        sa.Enum(OrgCreditAdjustmentReason, name="orgcreditadjustmentreason", native_enum=False),
        nullable=True,
    )

    actor_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_org_credit_ledger_actor_user_id", ondelete="SET NULL"),
        nullable=True,
    )

    account: Mapped[OrgCreditAccount] = relationship(
        "OrgCreditAccount",
        back_populates="ledger_entries",
        lazy="raise",
    )


class OrgCreditStatusHistory(AppendOnlyModel):
    __tablename__ = "org_credit_status_history"

    __table_args__ = (
        sa.Index("ix_org_credit_status_hist_org_created", "organization_id", "created_at"),
        sa.Index("ix_org_credit_status_hist_account", "credit_account_id"),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_org_credit_status_hist_org_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credit_account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_credit_accounts.id", name="fk_org_credit_status_hist_account_id", ondelete="CASCADE"),
        nullable=False,
    )

    from_status: Mapped[OrgCreditAccountStatus | None] = mapped_column(
        _org_credit_account_status_sa,
        nullable=True,
    )
    to_status: Mapped[OrgCreditAccountStatus] = mapped_column(
        _org_credit_account_status_sa,
        nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    actor_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_org_credit_status_hist_actor_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_user: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[actor_user_id],
        lazy="raise",
    )


class OrgCreditInternalScoreHistory(AppendOnlyModel):
    __tablename__ = "org_credit_internal_score_history"

    __table_args__ = (
        sa.Index("ix_org_credit_score_hist_org_created", "organization_id", "created_at"),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_org_credit_score_hist_org_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credit_account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_credit_accounts.id", name="fk_org_credit_score_hist_account_id", ondelete="CASCADE"),
        nullable=False,
    )

    score: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(20), nullable=False)

    breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    calculated_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_org_credit_score_hist_actor_id", ondelete="SET NULL"),
        nullable=True,
    )


class OrgCreditInvestigation(BaseModelNoVersion):
    __tablename__ = "org_credit_investigations"

    __table_args__ = (
        sa.Index(
            "uq_org_credit_investigation_active_per_org",
            "organization_id",
            unique=True,
            postgresql_where=sa.text("status = 'IN_PROGRESS'"),
        ),
        sa.Index("ix_org_credit_investigation_application_id", "application_id"),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_org_credit_investigation_organization_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    application_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey(
            "org_credit_applications.id",
            name="fk_org_credit_investigation_application_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    status: Mapped[OrgCreditInvestigationStatus] = mapped_column(
        sa.Enum(OrgCreditInvestigationStatus, name="orgcreditinvestigationstatus", native_enum=False),
        nullable=False,
        server_default=OrgCreditInvestigationStatus.IN_PROGRESS.value,
    )

    reg_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    country: Mapped[str | None] = mapped_column(String(10), nullable=True)

    provider_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    connect_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    requested_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_org_credit_investigation_requested_by_user_id", ondelete="SET NULL"),
        nullable=True,
    )

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_request: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
