"""Allow multiple suspension rule sets per scope+type.

Revision ID: 0090_multi_susp_rules_scope_type
Revises: 0089_org_service_tier_contract
Create Date: 2026-04-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0090_multi_susp_rules_scope_type"
down_revision: str | None = "0089_org_service_tier_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_unique_constraint(table_name: str, constraint_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(c["name"] == constraint_name for c in inspector.get_unique_constraints(table_name))


def _has_scope_type_duplicates() -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM suspension_rule_sets
            GROUP BY scope_type, scope_org_id, rule_type
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    return result is not None


def upgrade() -> None:
    if _has_unique_constraint("suspension_rule_sets", "uq_susp_rule_sets_scope_org_type"):
        op.drop_constraint("uq_susp_rule_sets_scope_org_type", "suspension_rule_sets", type_="unique")


def downgrade() -> None:
    # We cannot re-add the unique constraint while duplicate scope+type rows exist.
    if _has_scope_type_duplicates():
        raise RuntimeError(
            "Cannot downgrade: duplicate suspension_rule_sets rows exist for "
            "(scope_type, scope_org_id, rule_type)."
        )
    if not _has_unique_constraint("suspension_rule_sets", "uq_susp_rule_sets_scope_org_type"):
        op.create_unique_constraint(
            "uq_susp_rule_sets_scope_org_type",
            "suspension_rule_sets",
            ["scope_type", "scope_org_id", "rule_type"],
        )
