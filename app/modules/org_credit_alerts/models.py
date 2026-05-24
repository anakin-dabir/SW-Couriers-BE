from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import BaseModelNoVersion
from app.modules.org_credit_alerts.enums import (
    CreditAlertCooldownPeriod,
    CreditAlertDeliveryChannel,
    CreditAlertSeverity,
    CreditAlertStatus,
    CreditAlertType,
)
from app.modules.user.models import User


class OrgCreditAlertConfig(BaseModelNoVersion):
    """Per-organisation configuration for each credit alert type."""

    __tablename__ = "org_credit_alert_configs"

    __table_args__ = (
        sa.Index(
            "uq_org_credit_alert_configs_org_type",
            "organization_id",
            "alert_type",
            unique=True,
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_org_credit_alert_config_organization_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    alert_type: Mapped[CreditAlertType] = mapped_column(
        sa.Enum(CreditAlertType, name="creditalerttype", native_enum=False),
        nullable=False,
    )

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.true())

    threshold_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    score_drop_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reminder_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    late_payment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    cooldown_period: Mapped[CreditAlertCooldownPeriod] = mapped_column(
        sa.Enum(CreditAlertCooldownPeriod, name="creditalertcooldownperiod", native_enum=False),
        nullable=False,
        server_default=CreditAlertCooldownPeriod.ONE_HOUR.value,
    )

    delivery_channel: Mapped[CreditAlertDeliveryChannel] = mapped_column(
        sa.Enum(CreditAlertDeliveryChannel, name="creditalertdeliverychannel", native_enum=False),
        nullable=False,
        server_default=CreditAlertDeliveryChannel.BOTH.value,
    )

    auto_acknowledge: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.false())


class GlobalCreditAlertThreshold(BaseModelNoVersion):
    """System-wide default threshold percentages for utilisation alert types."""

    __tablename__ = "global_credit_alert_thresholds"

    alert_type: Mapped[CreditAlertType] = mapped_column(
        sa.Enum(CreditAlertType, name="creditalerttype", native_enum=False),
        nullable=False,
        unique=True,
    )

    threshold_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)


class OrgCreditAlert(BaseModelNoVersion):
    """A single fired alert — one row per firing."""

    __tablename__ = "org_credit_alerts"

    __table_args__ = (
        sa.Index("ix_org_credit_alerts_org_triggered_at", "organization_id", "triggered_at"),
        sa.Index("ix_org_credit_alerts_org_type_status", "organization_id", "alert_type", "status"),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="fk_org_credit_alert_organization_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    alert_type: Mapped[CreditAlertType] = mapped_column(
        sa.Enum(CreditAlertType, name="creditalerttype", native_enum=False),
        nullable=False,
    )

    severity: Mapped[CreditAlertSeverity] = mapped_column(
        sa.Enum(CreditAlertSeverity, name="creditalertseverity", native_enum=False),
        nullable=False,
    )

    status: Mapped[CreditAlertStatus] = mapped_column(
        sa.Enum(CreditAlertStatus, name="creditalertstatus", native_enum=False),
        nullable=False,
        server_default=CreditAlertStatus.ACTIVE.value,
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        index=True,
    )

    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_org_credit_alert_acknowledged_by_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    acknowledged_by: Mapped[User | None] = relationship(
        User,
        foreign_keys=[acknowledged_by_user_id],
        lazy="raise",
    )
