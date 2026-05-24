"""ORM models for canonical suspension rules and activity."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AppendOnlyModel, BaseModel, BaseModelNoVersion
from app.modules.organizations.enums import PaymentModel
from app.modules.suspension_rules.enums import (
    RuleScopeType,
    SuspensionConnector,
    SuspensionActionTaken,
    SuspensionRuleType,
    SuspensionRuleStatus,
)


class SuspensionActivity(AppendOnlyModel):
    """Append-only audit log of rule evaluations that triggered actions."""

    __tablename__ = "suspension_activity"

    # Canonical source-of-truth reference for activity rows.
    rule_set_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("suspension_rule_sets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rule_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)

    account_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)

    conditions_met: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        doc="Snapshot of condition values that satisfied the rule.",
    )

    action_taken: Mapped[str] = mapped_column(String(32), nullable=False, default=SuspensionActionTaken.SUSPENDED)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Canonical-v2 fields (nullable for backward compatibility with old rows)
    organization_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rule_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    payment_model: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("suspension_evaluation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    evaluated_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    group_results: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    final_result: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notification_status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    def __repr__(self) -> str:
        return f"<SuspensionActivity rule_set_id={self.rule_set_id} account_id={self.account_id} action={self.action_taken}>"


class SuspensionRuleSet(BaseModel):
    """Canonical scoped suspension ruleset (global default or org override)."""

    __tablename__ = "suspension_rule_sets"
    __table_args__ = (
        UniqueConstraint("name", name="uq_susp_rule_sets_name"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    condition_summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default=RuleScopeType.GLOBAL)
    scope_org_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    parent_global_rule_set_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("suspension_rule_sets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False, default=SuspensionRuleType.CREDIT_LIMIT)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=SuspensionRuleStatus.ACTIVE)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Action toggles shown in FE.
    auto_suspension_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pause_new_bookings: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    restrict_portal_login: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_finance_team: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_account_manager: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    conditions: Mapped[list["SuspensionRuleCondition"]] = relationship(
        "SuspensionRuleCondition",
        back_populates="rule_set",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class OrgSuspensionGlobalSuppression(BaseModelNoVersion):
    """Marks that a GLOBAL suspension ruleset must not appear in effective rules for one organisation."""

    __tablename__ = "org_suspension_global_suppressions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "global_rule_set_id",
            name="uq_org_susp_global_sup_org_global",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    global_rule_set_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("suspension_rule_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class SuspensionRuleCondition(BaseModelNoVersion):
    """Single ordered condition row inside a ruleset."""

    __tablename__ = "suspension_rule_conditions"
    __table_args__ = (
        UniqueConstraint("rule_set_id", "condition_type", name="uq_susp_rule_cond_unique_type_per_rule"),
        UniqueConstraint("rule_set_id", "position", name="uq_susp_rule_cond_unique_position_per_rule"),
    )

    rule_set_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("suspension_rule_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    connector: Mapped[str | None] = mapped_column(String(8), nullable=True, default=SuspensionConnector.NONE)
    condition_type: Mapped[str] = mapped_column(String(64), nullable=False)
    threshold_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(24), nullable=True)

    rule_set: Mapped[SuspensionRuleSet] = relationship("SuspensionRuleSet", back_populates="conditions", lazy="joined")


class SuspensionEvaluationRun(AppendOnlyModel):
    """One execution record for daily suspension evaluation job."""

    __tablename__ = "suspension_evaluation_runs"

    run_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING")
    evaluated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suspended_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class SuspensionNotificationAudit(BaseModelNoVersion):
    """External notification result attached to suspension activity."""

    __tablename__ = "suspension_notification_audit"

    activity_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("suspension_activity.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="EMAIL")
    recipient: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rule_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class PaymentRiskEvent(AppendOnlyModel):
    """Persisted payment-risk events used by card/cash suspension metrics."""

    __tablename__ = "payment_risk_events"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    order_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    payment_model: Mapped[str] = mapped_column(String(32), nullable=False, default=PaymentModel.CARD)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    rule_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
