"""add status automation rules tables

Revision ID: 0117_status_automation_rules
Revises: 0116_drop_orders_payment_status
Create Date: 2026-05-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0117_status_automation_rules"
down_revision: str | None = "0116_drop_orders_payment_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "status_automation_rule_sets",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False, server_default="GLOBAL"),
        sa.Column(
            "scope_org_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "parent_global_rule_set_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("status_automation_rule_sets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_type", "scope_org_id", "name", name="uq_status_auto_scope_name"),
        sa.CheckConstraint("priority >= 0 AND priority <= 1000", name="ck_status_auto_priority_range"),
    )
    op.create_index("ix_status_auto_rule_sets_scope_org_id", "status_automation_rule_sets", ["scope_org_id"])
    op.create_index(
        "ix_status_auto_rule_sets_parent_global_rule_set_id",
        "status_automation_rule_sets",
        ["parent_global_rule_set_id"],
    )
    op.create_index("ix_status_auto_rule_sets_status", "status_automation_rule_sets", ["status"])
    op.create_index("ix_status_auto_rule_sets_priority", "status_automation_rule_sets", ["priority"])

    op.create_table(
        "status_automation_triggers",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "rule_set_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("status_automation_rule_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("trigger_condition", sa.String(length=32), nullable=False),
        sa.Column("status_value", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_set_id", name="uq_status_auto_trigger_one_per_rule"),
    )
    op.create_index("ix_status_auto_triggers_rule_set_id", "status_automation_triggers", ["rule_set_id"])
    op.create_index("ix_status_auto_triggers_entity_type", "status_automation_triggers", ["entity_type"])

    op.create_table(
        "status_automation_conditions",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "rule_set_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("status_automation_rule_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("condition_type", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_set_id", "position", name="uq_status_auto_cond_position"),
    )
    op.create_index("ix_status_auto_conditions_rule_set_id", "status_automation_conditions", ["rule_set_id"])

    op.create_table(
        "status_automation_actions",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "rule_set_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("status_automation_rule_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("target_scope", sa.String(length=64), nullable=True),
        sa.Column("target_entity", sa.String(length=32), nullable=True),
        sa.Column("new_status", sa.String(length=64), nullable=True),
        sa.Column("module", sa.String(length=64), nullable=True),
        sa.Column("charge_type", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_set_id", "position", name="uq_status_auto_action_position"),
        sa.UniqueConstraint("rule_set_id", "action_type", name="uq_status_auto_action_type_once"),
    )
    op.create_index("ix_status_auto_actions_rule_set_id", "status_automation_actions", ["rule_set_id"])

    op.create_table(
        "status_automation_execution_logs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "rule_set_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("status_automation_rule_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="SUCCESS"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "rule_set_id", name="uq_status_auto_exec_dedupe"),
    )
    op.create_index("ix_status_auto_execution_logs_event_id", "status_automation_execution_logs", ["event_id"])
    op.create_index(
        "ix_status_auto_execution_logs_org_entity",
        "status_automation_execution_logs",
        ["organization_id", "entity_type", "entity_id"],
    )
    op.create_index("ix_status_auto_execution_logs_rule_set_id", "status_automation_execution_logs", ["rule_set_id"])


def downgrade() -> None:
    op.drop_index("ix_status_auto_execution_logs_rule_set_id", table_name="status_automation_execution_logs")
    op.drop_index("ix_status_auto_execution_logs_org_entity", table_name="status_automation_execution_logs")
    op.drop_index("ix_status_auto_execution_logs_event_id", table_name="status_automation_execution_logs")
    op.drop_table("status_automation_execution_logs")

    op.drop_index("ix_status_auto_actions_rule_set_id", table_name="status_automation_actions")
    op.drop_table("status_automation_actions")

    op.drop_index("ix_status_auto_conditions_rule_set_id", table_name="status_automation_conditions")
    op.drop_table("status_automation_conditions")

    op.drop_index("ix_status_auto_triggers_entity_type", table_name="status_automation_triggers")
    op.drop_index("ix_status_auto_triggers_rule_set_id", table_name="status_automation_triggers")
    op.drop_table("status_automation_triggers")

    op.drop_index("ix_status_auto_rule_sets_priority", table_name="status_automation_rule_sets")
    op.drop_index("ix_status_auto_rule_sets_status", table_name="status_automation_rule_sets")
    op.drop_index("ix_status_auto_rule_sets_parent_global_rule_set_id", table_name="status_automation_rule_sets")
    op.drop_index("ix_status_auto_rule_sets_scope_org_id", table_name="status_automation_rule_sets")
    op.drop_table("status_automation_rule_sets")

