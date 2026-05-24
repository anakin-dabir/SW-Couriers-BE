from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.modules.user.models import User

from app.common.models import AppendOnlyModel, BaseModelNoVersion
from app.modules.org_credit_settings.enums import ScheduledCreditSettingStatus


class GlobalCreditAccountCooldownPeriod(BaseModelNoVersion):
    __tablename__ = "global_credit_account_cooldown_periods"

    months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hours: Mapped[int | None] = mapped_column(Integer, nullable=True)


class OrgCreditAccountCooldownPeriod(BaseModelNoVersion):
    __tablename__ = "org_credit_account_cooldown_periods"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_org_credit_cooldown_period_org_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    months: Mapped[int] = mapped_column(Integer, nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    hours: Mapped[int] = mapped_column(Integer, nullable=False)


class OrgCreditCooldownWindow(BaseModelNoVersion):
    __tablename__ = "org_credit_cooldown_windows"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_org_credit_cooldown_window_org_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    policy_months: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_days: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_hours: Mapped[int] = mapped_column(Integer, nullable=False)


class OrgCreditTermsModificationHistory(AppendOnlyModel):
    __tablename__ = "org_credit_terms_modification_history"
    __table_args__ = (
        sa.Index("ix_org_credit_terms_hist_org_created", "organization_id", "created_at"),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_org_credit_terms_hist_org_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credit_account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_credit_accounts.id", name="fk_org_credit_terms_hist_acct_id", ondelete="CASCADE"),
        nullable=False,
    )
    old_payment_terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_payment_terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    modified_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_org_credit_terms_hist_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    modified_by_user: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[modified_by_user_id],
        lazy="raise",
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    applied_to_unpaid_invoices: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[ScheduledCreditSettingStatus] = mapped_column(
        sa.Enum(ScheduledCreditSettingStatus, name="schedcreditsettingstatus", native_enum=False),
        nullable=False,
        server_default=ScheduledCreditSettingStatus.APPLIED.value,
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrgCreditLimitAdjustmentHistory(AppendOnlyModel):
    __tablename__ = "org_credit_limit_adjustment_history"
    __table_args__ = (
        sa.Index("ix_oclah_org_created", "organization_id", "created_at"),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_oclah_org_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credit_account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_credit_accounts.id", name="fk_oclah_acct_id", ondelete="CASCADE"),
        nullable=False,
    )
    previous_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    new_limit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason_category: Mapped[str] = mapped_column(String(128), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    modified_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_oclah_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    modified_by_user: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[modified_by_user_id],
        lazy="raise",
    )
    status: Mapped[ScheduledCreditSettingStatus] = mapped_column(
        sa.Enum(ScheduledCreditSettingStatus, name="oclah_status", native_enum=False),
        nullable=False,
        server_default=ScheduledCreditSettingStatus.APPLIED.value,
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
