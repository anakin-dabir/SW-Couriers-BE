"""org_service_tier_contract_lines — per-org permitted/default tier contract.

Revision ID: 0089_org_service_tier_contract
Revises: 0088_delivery_attempt_configs
Create Date: 2026-04-27

Stores which global template each org uses, standard vs custom, optional ORG tier row
for custom values, and permitted/default flags for booking.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0089_org_service_tier_contract"
down_revision: str | None = "0088_delivery_attempt_configs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_service_tier_contract_lines",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("global_template_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("permitted", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("org_tier_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("mode IN ('standard', 'custom')", name="ck_org_st_contract_mode"),
        sa.CheckConstraint(
            "(mode = 'standard' AND org_tier_id IS NULL) OR (mode = 'custom' AND org_tier_id IS NOT NULL)",
            name="ck_org_st_contract_mode_org_tier",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["global_template_id"], ["service_tier.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["org_tier_id"], ["service_tier.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "global_template_id", name="uq_org_st_contract_org_global"),
    )
    op.create_index("ix_org_st_contract_organization_id", "org_service_tier_contract_lines", ["organization_id"])
    op.create_index("ix_org_st_contract_global_template_id", "org_service_tier_contract_lines", ["global_template_id"])
    op.create_index("ix_org_st_contract_org_tier_id", "org_service_tier_contract_lines", ["org_tier_id"])
    op.create_index(
        "uq_org_st_contract_one_default_per_org",
        "org_service_tier_contract_lines",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_org_st_contract_one_default_per_org", table_name="org_service_tier_contract_lines")
    op.drop_index("ix_org_st_contract_org_tier_id", table_name="org_service_tier_contract_lines")
    op.drop_index("ix_org_st_contract_global_template_id", table_name="org_service_tier_contract_lines")
    op.drop_index("ix_org_st_contract_organization_id", table_name="org_service_tier_contract_lines")
    op.drop_table("org_service_tier_contract_lines")
