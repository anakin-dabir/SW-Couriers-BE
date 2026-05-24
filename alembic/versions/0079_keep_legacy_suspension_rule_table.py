"""Keep legacy suspension_rule table while backfilling canonical links.

Revision ID: 0079_keep_legacy_susp_rule
Revises: 0078_activity_ruleset_bridge
Create Date: 2026-04-20 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0079_keep_legacy_susp_rule"
down_revision: str | None = "0078_activity_ruleset_bridge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_table("suspension_activity"):
        return

    # Keep legacy schema intact for now.
    # Only backfill canonical links when possible so newer code can read rule_set_id.
    if _has_column("suspension_activity", "rule_set_id") and _has_column("suspension_activity", "rule_id"):
        op.execute(
            sa.text(
                """
                UPDATE suspension_activity sa
                SET rule_set_id = sa.rule_id
                WHERE sa.rule_set_id IS NULL
                  AND sa.rule_id IS NOT NULL
                  AND EXISTS (
                    SELECT 1
                    FROM suspension_rule_sets rs
                    WHERE rs.id = sa.rule_id
                  )
                """
            )
        )


def downgrade() -> None:
    # Intentionally a no-op: upgrade is non-destructive and preserves legacy schema.
    return
