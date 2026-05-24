"""ORM models for status automation scoped rules."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import BaseModel, BaseModelNoVersion
from app.modules.status_automation_rules.enums import StatusAutomationRuleStatus, StatusAutomationScopeType


class StatusAutomationRuleSet(BaseModel):
    __tablename__ = "status_automation_rule_sets"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_org_id", "name", name="uq_status_auto_scope_name"),
        CheckConstraint("priority >= 0 AND priority <= 1000", name="ck_status_auto_priority_range"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default=StatusAutomationScopeType.GLOBAL)
    scope_org_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    parent_global_rule_set_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("status_automation_rule_sets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=StatusAutomationRuleStatus.ACTIVE)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    trigger: Mapped["StatusAutomationTrigger"] = relationship(
        "StatusAutomationTrigger",
        back_populates="rule_set",
        uselist=False,
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    conditions: Mapped[list["StatusAutomationCondition"]] = relationship(
        "StatusAutomationCondition",
        back_populates="rule_set",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    actions: Mapped[list["StatusAutomationAction"]] = relationship(
        "StatusAutomationAction",
        back_populates="rule_set",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class StatusAutomationTrigger(BaseModelNoVersion):
    __tablename__ = "status_automation_triggers"
    __table_args__ = (UniqueConstraint("rule_set_id", name="uq_status_auto_trigger_one_per_rule"),)

    rule_set_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("status_automation_rule_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status_value: Mapped[str] = mapped_column(String(64), nullable=False)

    rule_set: Mapped[StatusAutomationRuleSet] = relationship(
        "StatusAutomationRuleSet",
        back_populates="trigger",
        lazy="joined",
    )


class StatusAutomationCondition(BaseModelNoVersion):
    __tablename__ = "status_automation_conditions"
    __table_args__ = (
        UniqueConstraint("rule_set_id", name="uq_status_auto_cond_one_per_rule"),
    )

    rule_set_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("status_automation_rule_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    value: Mapped[str] = mapped_column(String(64), nullable=False)

    rule_set: Mapped[StatusAutomationRuleSet] = relationship(
        "StatusAutomationRuleSet",
        back_populates="conditions",
        lazy="joined",
    )


class StatusAutomationAction(BaseModelNoVersion):
    __tablename__ = "status_automation_actions"
    __table_args__ = (UniqueConstraint("rule_set_id", name="uq_status_auto_action_one_per_rule"),)

    rule_set_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("status_automation_rule_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    new_status: Mapped[str] = mapped_column(String(64), nullable=False)

    rule_set: Mapped[StatusAutomationRuleSet] = relationship(
        "StatusAutomationRuleSet",
        back_populates="actions",
        lazy="joined",
    )


class StatusAutomationExecutionLog(BaseModelNoVersion):
    __tablename__ = "status_automation_execution_logs"
    __table_args__ = (
        UniqueConstraint("event_id", "rule_set_id", name="uq_status_auto_exec_dedupe"),
    )

    event_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    organization_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    rule_set_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("status_automation_rule_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SUCCESS")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

