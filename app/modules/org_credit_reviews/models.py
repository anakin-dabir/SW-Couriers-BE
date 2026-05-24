from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Date, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import BaseModelNoVersion
from app.modules.org_credit.enums import OrgCreditReviewFrequency
from app.modules.org_credit.models import OrgCreditAccount
from app.modules.org_credit_reviews.enums import CreditReviewOutcome, CreditReviewRiskLevel
from app.modules.user.models import User


class OrgCreditReview(BaseModelNoVersion):
    __tablename__ = "org_credit_reviews"

    __table_args__ = (
        sa.Index("ix_org_credit_reviews_org_review_date", "organization_id", "review_date"),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_org_credit_review_organization_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_credit_accounts.id", name="fk_org_credit_review_account_id", ondelete="CASCADE"),
        nullable=False,
    )

    reviewer_user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_org_credit_review_reviewer_user_id", ondelete="SET NULL"),
        nullable=False,
    )

    review_date: Mapped[date] = mapped_column(Date, nullable=False)

    review_frequency_at_time: Mapped[OrgCreditReviewFrequency | None] = mapped_column(
        sa.Enum(OrgCreditReviewFrequency, name="orgcreditreviewfrequency", native_enum=False, create_constraint=False),
        nullable=True,
    )

    risk_level: Mapped[CreditReviewRiskLevel] = mapped_column(
        sa.Enum(CreditReviewRiskLevel, name="creditreviewrisklevel", native_enum=False),
        nullable=False,
    )

    outcome: Mapped[CreditReviewOutcome] = mapped_column(
        sa.Enum(CreditReviewOutcome, name="creditreviewoutcome", native_enum=False),
        nullable=False,
    )

    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    next_review_frequency: Mapped[OrgCreditReviewFrequency | None] = mapped_column(
        sa.Enum(OrgCreditReviewFrequency, name="orgcreditreviewfrequency", native_enum=False, create_constraint=False),
        nullable=True,
    )

    recommended_new_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    recommended_payment_terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    credit_report_snapshot_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_credit_reports.id", name="fk_org_credit_review_report_snapshot_id", ondelete="SET NULL"),
        nullable=True,
    )

    reviewer: Mapped[User | None] = relationship(
        User,
        foreign_keys=[reviewer_user_id],
        lazy="raise",
    )

    account: Mapped[OrgCreditAccount] = relationship(
        OrgCreditAccount,
        lazy="raise",
    )
