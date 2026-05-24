"""drop_pricing_tiers_seed_service_tier

Drops the pricing_tiers table (replaced by service_tier) and ensures the
service_tier table exists with the full schema (description, status columns
and nullable color/icon). Seeds the three default tiers so admins have a
starting set — they can create/edit/delete as needed.

Revision ID: 0017_drop_pricing_tiers_seed_service_tier
Revises: 0016_org_payment_config
Create Date: 2026-03-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0017_drop_pricing_tiers"
down_revision: str | None = "0016_org_payment_config"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    # ── 1. Drop the redundant pricing_tiers table ──────────────────────────
    if "pricing_tiers" in existing_tables:
        op.drop_table("pricing_tiers")

    # ── 2. Ensure service_tier exists with the full schema ─────────────────
    if "service_tier" not in existing_tables:
        # Table was never created (migration was stamped, not run) — create it now
        op.create_table(
            "service_tier",
            sa.Column("tier_name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("duration_days", sa.Integer, nullable=False),
            sa.Column("price_per_package", sa.Numeric(10, 2), nullable=False),
            sa.Column("available_for", sa.String(32), nullable=False),
            sa.Column("color", sa.String(16), nullable=True),
            sa.Column("icon", sa.String(64), nullable=True),
            sa.Column("status", sa.String(10), nullable=False, server_default="ACTIVE"),
            sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("version", sa.Integer, server_default="1", nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tier_name", "available_for", name="uq_service_tier_name_available_for"),
        )
    else:
        # Table already exists — add the new columns if missing
        existing_cols = {c["name"] for c in inspector.get_columns("service_tier")}
        if "description" not in existing_cols:
            op.add_column("service_tier", sa.Column("description", sa.Text, nullable=True))
        if "status" not in existing_cols:
            op.add_column(
                "service_tier",
                sa.Column("status", sa.String(10), nullable=False, server_default="ACTIVE"),
            )
        if "color" in existing_cols:
            op.alter_column("service_tier", "color", nullable=True)
        if "icon" in existing_cols:
            op.alter_column("service_tier", "icon", nullable=True)

    # ── 3. Seed default service tiers ──────────────────────────────────────
    op.execute("""
        INSERT INTO service_tier (
            id, tier_name, duration_days, price_per_package,
            available_for, color, icon, status, version, created_at, updated_at
        )
        VALUES
            (
                gen_random_uuid(), 'Basic', 30, 50.85,
                'CUSTOMER_B2B', '#4A90D9', 'truck', 'ACTIVE',
                1, now(), now()
            ),
            (
                gen_random_uuid(), 'Plus', 60, 70.87,
                'BOTH', '#7B68EE', 'clock', 'ACTIVE',
                1, now(), now()
            ),
            (
                gen_random_uuid(), 'Professional', 90, 100.00,
                'CUSTOMER_B2C', '#50C878', 'star', 'ACTIVE',
                1, now(), now()
            )
        ON CONFLICT (tier_name, available_for) DO NOTHING
    """)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if "service_tier" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("service_tier")}
        if "status" in existing_cols:
            op.drop_column("service_tier", "status")
        if "description" in existing_cols:
            op.drop_column("service_tier", "description")
        if "color" in existing_cols:
            op.alter_column("service_tier", "color", nullable=False)
        if "icon" in existing_cols:
            op.alter_column("service_tier", "icon", nullable=False)

    # Re-create pricing_tiers (data is lost on downgrade)
    if "pricing_tiers" not in existing_tables:
        op.create_table(
            "pricing_tiers",
            sa.Column("id", sa.String(36), primary_key=True, nullable=False),
            sa.Column("name", sa.String(100), nullable=False, unique=True),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("color", sa.String(30), nullable=True),
            sa.Column("icon", sa.String(100), nullable=True),
            sa.Column("price", sa.Numeric(10, 2), nullable=False),
            sa.Column("duration_days", sa.Integer, nullable=False),
            sa.Column("available_for", sa.String(10), nullable=False, server_default="BOTH"),
            sa.Column("status", sa.String(10), nullable=False, server_default="ACTIVE"),
            sa.Column("version", sa.Integer, nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
