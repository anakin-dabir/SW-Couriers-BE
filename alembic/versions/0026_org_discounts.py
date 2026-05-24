"""Add org_discount_configs table.

Supports three independently-enabled discount types per organisation:
- Percentage Discount (flat % off each booking invoice)
- Fixed Discount per Booking (fixed GBP amount off each booking)
- Volume Discount Tiered (% discount based on monthly booking volume, stored as JSONB)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_org_discounts"
down_revision: str | None = "0025_drop_driver_capacity_column"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "org_discount_configs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),

        # Percentage Discount
        sa.Column("percentage_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("percentage_value", sa.Numeric(5, 2), nullable=True),
        sa.Column("percentage_valid_from", sa.Date(), nullable=True),
        sa.Column("percentage_valid_until", sa.Date(), nullable=True),

        # Fixed Discount per Booking
        sa.Column("fixed_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("fixed_value", sa.Numeric(10, 2), nullable=True),
        sa.Column("fixed_valid_from", sa.Date(), nullable=True),
        sa.Column("fixed_valid_until", sa.Date(), nullable=True),

        # Volume Discount (Tiered) — JSONB array of tier objects
        sa.Column("volume_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("volume_tiers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),

        # BaseModel standard columns
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),

        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", name="uq_org_discount_configs_org_id"),
    )
    op.create_index("ix_org_discount_configs_organization_id", "org_discount_configs", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_org_discount_configs_organization_id", table_name="org_discount_configs")
    op.drop_table("org_discount_configs")
