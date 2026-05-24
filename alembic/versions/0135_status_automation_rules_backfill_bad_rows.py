"""repair legacy/invalid status automation rows for v2 runtime

Revision ID: 0135_status_automation
Revises: 0134_backfill_route_plan
Create Date: 2026-05-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0135_status_automation"
down_revision: str | None = "0134_backfill_route_plan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Guard against orphan children from partially applied legacy migrations.
    op.execute(
        """
        DELETE FROM status_automation_execution_logs l
        WHERE NOT EXISTS (
            SELECT 1 FROM status_automation_rule_sets rs WHERE rs.id = l.rule_set_id
        )
        """
    )
    op.execute(
        """
        DELETE FROM status_automation_actions a
        WHERE NOT EXISTS (
            SELECT 1 FROM status_automation_rule_sets rs WHERE rs.id = a.rule_set_id
        )
        """
    )
    op.execute(
        """
        DELETE FROM status_automation_conditions c
        WHERE NOT EXISTS (
            SELECT 1 FROM status_automation_rule_sets rs WHERE rs.id = c.rule_set_id
        )
        """
    )
    op.execute(
        """
        DELETE FROM status_automation_triggers t
        WHERE NOT EXISTS (
            SELECT 1 FROM status_automation_rule_sets rs WHERE rs.id = t.rule_set_id
        )
        """
    )

    # 2) Normalize enum-like text fields to values understood by current code.
    op.execute(
        """
        UPDATE status_automation_rule_sets
        SET status = 'INACTIVE'
        WHERE status NOT IN ('ACTIVE', 'INACTIVE') OR status IS NULL
        """
    )
    op.execute(
        """
        UPDATE status_automation_rule_sets
        SET scope_type = 'GLOBAL', scope_org_id = NULL
        WHERE scope_type NOT IN ('GLOBAL', 'ORG') OR scope_type IS NULL
        """
    )
    op.execute(
        """
        UPDATE status_automation_rule_sets
        SET scope_org_id = NULL
        WHERE scope_type = 'GLOBAL'
        """
    )

    # ORG rules without org scope cannot be recovered safely; deactivate by deleting.
    op.execute(
        """
        DELETE FROM status_automation_rule_sets
        WHERE scope_type = 'ORG' AND scope_org_id IS NULL
        """
    )

    # 3) Keep at most one trigger/action/condition row per rule_set (latest wins).
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY rule_set_id
                    ORDER BY created_at DESC, id DESC
                ) AS rn
            FROM status_automation_triggers
        )
        DELETE FROM status_automation_triggers t
        USING ranked r
        WHERE t.id = r.id AND r.rn > 1
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY rule_set_id
                    ORDER BY created_at DESC, id DESC
                ) AS rn
            FROM status_automation_actions
        )
        DELETE FROM status_automation_actions a
        USING ranked r
        WHERE a.id = r.id AND r.rn > 1
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY rule_set_id
                    ORDER BY created_at DESC, id DESC
                ) AS rn
            FROM status_automation_conditions
        )
        DELETE FROM status_automation_conditions c
        USING ranked r
        WHERE c.id = r.id AND r.rn > 1
        """
    )

    # 4) Remove trigger/condition rows with enum values no longer accepted by v2 schemas.
    op.execute(
        """
        DELETE FROM status_automation_triggers
        WHERE entity_type NOT IN ('PACKAGE', 'DELIVERY_STOP', 'BOOKING_ORDER')
        """
    )
    op.execute(
        """
        DELETE FROM status_automation_conditions
        WHERE value <> 'AFTER_PICKUP'
        """
    )

    # 5) Ensure every rule has required trigger + action so list/create/read APIs can't crash on null graph nodes.
    op.execute(
        """
        DELETE FROM status_automation_rule_sets rs
        WHERE NOT EXISTS (
            SELECT 1 FROM status_automation_triggers t WHERE t.rule_set_id = rs.id
        )
        OR NOT EXISTS (
            SELECT 1 FROM status_automation_actions a WHERE a.rule_set_id = rs.id
        )
        """
    )

    # 6) Enforce timing semantics for v2 graph:
    #    - timing allowed only for CANCELLED trigger
    #    - CANCELLED trigger requires timing (otherwise deactivate rule)
    op.execute(
        """
        DELETE FROM status_automation_conditions c
        USING status_automation_triggers t
        WHERE c.rule_set_id = t.rule_set_id
          AND t.status_value <> 'CANCELLED'
        """
    )
    op.execute(
        """
        UPDATE status_automation_rule_sets rs
        SET status = 'INACTIVE'
        WHERE EXISTS (
            SELECT 1
            FROM status_automation_triggers t
            WHERE t.rule_set_id = rs.id
              AND t.status_value = 'CANCELLED'
        )
        AND NOT EXISTS (
            SELECT 1
            FROM status_automation_conditions c
            WHERE c.rule_set_id = rs.id
        )
        """
    )


def downgrade() -> None:
    # Data-healing migration; no reversible structural changes.
    pass
