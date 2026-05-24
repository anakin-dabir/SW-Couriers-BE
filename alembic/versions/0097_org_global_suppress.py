"""Per-org opt-out rows for GLOBAL suspension rule templates.

Revision ID: 0097_org_global_suppress
Revises: 0096_susp_rules_parent_global
Create Date: 2026-04-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0097_org_global_suppress"
down_revision: str | None = "0096_susp_rules_parent_global"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_suspension_global_suppressions",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("global_rule_set_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["global_rule_set_id"], ["suspension_rule_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "global_rule_set_id",
            name="uq_org_susp_global_sup_org_global",
        ),
    )
    op.create_index(
        "ix_org_susp_global_suppressions_organization_id",
        "org_suspension_global_suppressions",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_org_susp_global_suppressions_organization_id",
        table_name="org_suspension_global_suppressions",
    )
    op.drop_table("org_suspension_global_suppressions")
