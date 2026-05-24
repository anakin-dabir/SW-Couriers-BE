"""Bridge suspension_activity to canonical rule_set_id.

Revision ID: 0078_activity_ruleset_bridge
Revises: 0077_suspension_rules_refactor
Create Date: 2026-04-20 00:00:00.000000

This migration introduces suspension_activity.rule_set_id as the canonical FK to
suspension_rule_sets while keeping legacy rule_id in place during transition.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0078_activity_ruleset_bridge"
down_revision: str | None = "0077_suspension_rules_refactor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_table("suspension_activity"):
        return

    if not _has_column("suspension_activity", "rule_set_id"):
        op.add_column("suspension_activity", sa.Column("rule_set_id", postgresql.UUID(as_uuid=False), nullable=True))

    fk_names = [fk["name"] for fk in sa.inspect(op.get_bind()).get_foreign_keys("suspension_activity")]
    if "fk_suspension_activity_rule_set_id" not in fk_names:
        op.create_foreign_key(
            "fk_suspension_activity_rule_set_id",
            "suspension_activity",
            "suspension_rule_sets",
            ["rule_set_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if not _has_index("suspension_activity", "ix_suspension_activity_rule_set_id"):
        op.create_index("ix_suspension_activity_rule_set_id", "suspension_activity", ["rule_set_id"])

    # Backfill canonical rule_set_id when existing rule_id already maps to rule_set id.
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

    # Allow null legacy rule_id for rows that only have canonical rule_set linkage.
    if _has_column("suspension_activity", "rule_id"):
        op.alter_column("suspension_activity", "rule_id", existing_type=postgresql.UUID(as_uuid=False), nullable=True)


def downgrade() -> None:
    if not _has_table("suspension_activity"):
        return

    fk_names = [fk["name"] for fk in sa.inspect(op.get_bind()).get_foreign_keys("suspension_activity")]
    if "fk_suspension_activity_rule_set_id" in fk_names:
        op.drop_constraint("fk_suspension_activity_rule_set_id", "suspension_activity", type_="foreignkey")
    if _has_index("suspension_activity", "ix_suspension_activity_rule_set_id"):
        op.drop_index("ix_suspension_activity_rule_set_id", table_name="suspension_activity")
    if _has_column("suspension_activity", "rule_set_id"):
        op.drop_column("suspension_activity", "rule_set_id")
