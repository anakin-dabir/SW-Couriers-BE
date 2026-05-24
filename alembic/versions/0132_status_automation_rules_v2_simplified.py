"""simplify status automation schema for v2 flow

Revision ID: 0132_status_automation_rules
Revises: 0131_pickup_contact_phone
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0132_status_automation_rules"
down_revision: str | None = "0131_pickup_contact_phone"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # v2 rollout is manual-recreate only; legacy rule rows are intentionally cleared.
    op.execute(
        "TRUNCATE TABLE "
        "status_automation_execution_logs, "
        "status_automation_actions, "
        "status_automation_conditions, "
        "status_automation_triggers, "
        "status_automation_rule_sets "
        "RESTART IDENTITY CASCADE"
    )

    op.drop_column("status_automation_triggers", "scope")
    op.drop_column("status_automation_triggers", "trigger_condition")

    op.drop_constraint("uq_status_auto_cond_position", "status_automation_conditions", type_="unique")
    op.drop_column("status_automation_conditions", "position")
    op.drop_column("status_automation_conditions", "condition_type")
    op.create_unique_constraint("uq_status_auto_cond_one_per_rule", "status_automation_conditions", ["rule_set_id"])

    op.drop_constraint("uq_status_auto_action_position", "status_automation_actions", type_="unique")
    op.drop_constraint("uq_status_auto_action_type_once", "status_automation_actions", type_="unique")
    op.drop_column("status_automation_actions", "position")
    op.drop_column("status_automation_actions", "action_type")
    op.drop_column("status_automation_actions", "target_scope")
    op.drop_column("status_automation_actions", "target_entity")
    op.drop_column("status_automation_actions", "module")
    op.drop_column("status_automation_actions", "charge_type")
    op.alter_column("status_automation_actions", "new_status", existing_type=sa.String(length=64), nullable=False)
    op.create_unique_constraint("uq_status_auto_action_one_per_rule", "status_automation_actions", ["rule_set_id"])


def downgrade() -> None:
    op.drop_constraint("uq_status_auto_action_one_per_rule", "status_automation_actions", type_="unique")
    op.alter_column("status_automation_actions", "new_status", existing_type=sa.String(length=64), nullable=True)
    op.add_column("status_automation_actions", sa.Column("charge_type", sa.String(length=64), nullable=True))
    op.add_column("status_automation_actions", sa.Column("module", sa.String(length=64), nullable=True))
    op.add_column("status_automation_actions", sa.Column("target_entity", sa.String(length=32), nullable=True))
    op.add_column("status_automation_actions", sa.Column("target_scope", sa.String(length=64), nullable=True))
    op.add_column(
        "status_automation_actions",
        sa.Column("action_type", sa.String(length=32), nullable=False, server_default="CHANGE_STATUS"),
    )
    op.add_column(
        "status_automation_actions",
        sa.Column("position", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("status_automation_actions", "action_type", server_default=None)
    op.alter_column("status_automation_actions", "position", server_default=None)
    op.create_unique_constraint("uq_status_auto_action_position", "status_automation_actions", ["rule_set_id", "position"])
    op.create_unique_constraint("uq_status_auto_action_type_once", "status_automation_actions", ["rule_set_id", "action_type"])

    op.drop_constraint("uq_status_auto_cond_one_per_rule", "status_automation_conditions", type_="unique")
    op.add_column(
        "status_automation_conditions",
        sa.Column("condition_type", sa.String(length=64), nullable=False, server_default="TIMING_IS"),
    )
    op.add_column(
        "status_automation_conditions",
        sa.Column("position", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("status_automation_conditions", "condition_type", server_default=None)
    op.alter_column("status_automation_conditions", "position", server_default=None)
    op.create_unique_constraint("uq_status_auto_cond_position", "status_automation_conditions", ["rule_set_id", "position"])

    op.add_column("status_automation_triggers", sa.Column("trigger_condition", sa.String(length=32), nullable=False, server_default="STATUS_CHANGES_TO"))
    op.add_column("status_automation_triggers", sa.Column("scope", sa.String(length=64), nullable=False, server_default="THIS_PACKAGE"))
    op.alter_column("status_automation_triggers", "trigger_condition", server_default=None)
    op.alter_column("status_automation_triggers", "scope", server_default=None)
