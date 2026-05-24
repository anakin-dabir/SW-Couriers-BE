"""Add parent-global link for org suspension customisations.

Revision ID: 0096_susp_rules_parent_global
Revises: 0095_org_account_managers
Create Date: 2026-04-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0096_susp_rules_parent_global"
down_revision: str | None = "0095_org_account_managers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
    if not _has_column("suspension_rule_sets", "parent_global_rule_set_id"):
        op.add_column(
            "suspension_rule_sets",
            sa.Column(
                "parent_global_rule_set_id",
                postgresql.UUID(as_uuid=False),
                sa.ForeignKey("suspension_rule_sets.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    if not _has_index("suspension_rule_sets", "ix_suspension_rule_sets_parent_global_rule_set_id"):
        op.create_index(
            "ix_suspension_rule_sets_parent_global_rule_set_id",
            "suspension_rule_sets",
            ["parent_global_rule_set_id"],
        )
    if not _has_index("suspension_rule_sets", "uq_susp_rule_sets_org_parent_active"):
        op.create_index(
            "uq_susp_rule_sets_org_parent_active",
            "suspension_rule_sets",
            ["scope_org_id", "parent_global_rule_set_id"],
            unique=True,
            postgresql_where=sa.text(
                "scope_type = 'ORG' AND parent_global_rule_set_id IS NOT NULL AND status = 'ACTIVE'"
            ),
        )


def downgrade() -> None:
    if _has_index("suspension_rule_sets", "uq_susp_rule_sets_org_parent_active"):
        op.drop_index("uq_susp_rule_sets_org_parent_active", table_name="suspension_rule_sets")
    if _has_index("suspension_rule_sets", "ix_suspension_rule_sets_parent_global_rule_set_id"):
        op.drop_index("ix_suspension_rule_sets_parent_global_rule_set_id", table_name="suspension_rule_sets")
    if _has_column("suspension_rule_sets", "parent_global_rule_set_id"):
        op.drop_column("suspension_rule_sets", "parent_global_rule_set_id")
