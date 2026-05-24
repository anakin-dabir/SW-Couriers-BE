"""settings_pricing_tiers

Creates the pricing_tiers table for admin-managed service pricing configuration.

Revision ID: 0013_settings_pricing_tiers
Revises: 0012_org_extended
Create Date: 2026-03-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_settings_pricing_tiers"
down_revision: str | None = "0012_org_extended"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pricing_tiers",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("color", sa.String(30), nullable=True),
        sa.Column("icon", sa.String(100), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("duration_days", sa.Integer, nullable=False),
        sa.Column(
            "available_for",
            sa.String(10),
            nullable=False,
            server_default="BOTH",
        ),
        sa.Column(
            "status",
            sa.String(10),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Seed the three default tiers shown in the UI
    op.execute("""
        INSERT INTO pricing_tiers (id, name, description, price, duration_days, available_for, status)
        VALUES
            (gen_random_uuid(), 'Basic',        NULL, 50.85,  30, 'B2B',  'ACTIVE'),
            (gen_random_uuid(), 'Plus',         NULL, 70.87,  60, 'BOTH', 'ACTIVE'),
            (gen_random_uuid(), 'Professional', NULL, 100.00, 90, 'B2C',  'ACTIVE')
    """)


def downgrade() -> None:
    op.drop_table("pricing_tiers")
