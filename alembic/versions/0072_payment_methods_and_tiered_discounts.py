"""Restructure payment config and discount configs.

Changes:
1. Drop and recreate org_discount_configs:
   - Old: single row per org with flat percentage/fixed/volume columns.
   - New: one row per (organization_id, service_tier_id, discount_type).
     Each row has is_enabled, value/valid_from/valid_until (PERCENTAGE &
     FIXED_PER_BOOKING), and volume_tiers JSONB (VOLUME_TIERED).

2. Alter org_payment_configs:
   - Drop payment-method-specific columns that move to org_payment_methods:
     payment_model, billing_schedule, billing_day_of_month,
     billing_days_after_order, bank_account_name, bank_account_number,
     bank_sort_code, credit_limit, credit_utilization_warning_pct,
     return_to_sender_fee.
   - Add new shared-config columns:
     vat_number, max_return_attempts, return_attempt_fees,
     weight_margin_kg, weight_surcharge_per_kg.

3. Create org_payment_methods table:
   - One row per (organization_id, payment_model) — UNIQUE constraint.
   - Carries: billing_schedule, billing_day_of_month, billing_days_after_order,
     bank details, credit settings, is_default flag.
   - Adds CASH to supported payment models.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0072_payment_methods_tiered_disc"
down_revision: str | None = "0071_driver_stop_exec"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── 1. Recreate org_discount_configs ──────────────────────────────────────
    op.drop_index("ix_org_discount_configs_organization_id", table_name="org_discount_configs")
    op.drop_table("org_discount_configs")

    op.create_table(
        "org_discount_configs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("service_tier_id", postgresql.UUID(as_uuid=False), nullable=False),
        # discount_type: PERCENTAGE | FIXED_PER_BOOKING | VOLUME_TIERED
        sa.Column("discount_type", sa.String(length=32), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),

        # PERCENTAGE / FIXED_PER_BOOKING fields (null for VOLUME_TIERED)
        sa.Column("value", sa.Numeric(10, 2), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),

        # VOLUME_TIERED field (null for other types)
        sa.Column("volume_tiers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),

        # BaseModel standard columns
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),

        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_tier_id"], ["service_tier.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "service_tier_id", "discount_type",
            name="uq_org_discount_org_tier_type",
        ),
    )
    op.create_index("ix_org_discount_configs_organization_id", "org_discount_configs", ["organization_id"])
    op.create_index("ix_org_discount_configs_service_tier_id", "org_discount_configs", ["service_tier_id"])
    op.create_index("ix_org_discount_configs_discount_type", "org_discount_configs", ["discount_type"])

    # ── 2. Alter org_payment_configs ──────────────────────────────────────────
    # Drop columns that move to org_payment_methods
    op.drop_column("org_payment_configs", "payment_model")
    op.drop_column("org_payment_configs", "billing_schedule")
    op.drop_column("org_payment_configs", "billing_day_of_month")
    op.drop_column("org_payment_configs", "billing_days_after_order")
    op.drop_column("org_payment_configs", "bank_account_name")
    op.drop_column("org_payment_configs", "bank_account_number")
    op.drop_column("org_payment_configs", "bank_sort_code")
    op.drop_column("org_payment_configs", "credit_limit")
    op.drop_column("org_payment_configs", "credit_utilization_warning_pct")
    op.drop_column("org_payment_configs", "return_to_sender_fee")

    # Add new shared-config columns
    op.add_column("org_payment_configs", sa.Column("vat_number", sa.String(50), nullable=True))
    op.add_column("org_payment_configs", sa.Column("max_return_attempts", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("org_payment_configs", sa.Column("return_attempt_fees", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("org_payment_configs", sa.Column("weight_margin_kg", sa.Float(), nullable=True))
    op.add_column("org_payment_configs", sa.Column("weight_surcharge_per_kg", sa.Numeric(10, 2), nullable=True))

    # ── 3. Create org_payment_methods ─────────────────────────────────────────
    op.create_table(
        "org_payment_methods",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
        # payment_model: CARD | BANK_TRANSFER | CREDIT_ACCOUNT | CASH
        sa.Column("payment_model", sa.String(length=32), nullable=False),
        sa.Column("billing_schedule", sa.String(length=32), nullable=False),
        sa.Column("billing_day_of_month", sa.Integer(), nullable=True),
        sa.Column("billing_days_after_order", sa.Integer(), nullable=True),
        sa.Column("bank_account_name", sa.String(255), nullable=True),
        sa.Column("bank_account_number", sa.String(50), nullable=True),
        sa.Column("bank_sort_code", sa.String(20), nullable=True),
        sa.Column("credit_limit", sa.Numeric(12, 2), nullable=True),
        sa.Column("credit_utilization_warning_pct", sa.Integer(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),

        # BaseModel standard columns
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),

        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "payment_model",
            name="uq_org_payment_methods_org_model",
        ),
    )
    op.create_index("ix_org_payment_methods_organization_id", "org_payment_methods", ["organization_id"])
    op.create_index("ix_org_payment_methods_is_default", "org_payment_methods", ["is_default"])


def downgrade() -> None:
    # ── 3. Drop org_payment_methods ───────────────────────────────────────────
    op.drop_index("ix_org_payment_methods_is_default", table_name="org_payment_methods")
    op.drop_index("ix_org_payment_methods_organization_id", table_name="org_payment_methods")
    op.drop_table("org_payment_methods")

    # ── 2. Revert org_payment_configs ─────────────────────────────────────────
    op.drop_column("org_payment_configs", "weight_surcharge_per_kg")
    op.drop_column("org_payment_configs", "weight_margin_kg")
    op.drop_column("org_payment_configs", "return_attempt_fees")
    op.drop_column("org_payment_configs", "max_return_attempts")
    op.drop_column("org_payment_configs", "vat_number")

    op.add_column("org_payment_configs", sa.Column("payment_model", sa.String(32), nullable=False, server_default="CARD"))
    op.add_column("org_payment_configs", sa.Column("billing_schedule", sa.String(32), nullable=False, server_default="IMMEDIATE"))
    op.add_column("org_payment_configs", sa.Column("billing_day_of_month", sa.Integer(), nullable=True))
    op.add_column("org_payment_configs", sa.Column("billing_days_after_order", sa.Integer(), nullable=True))
    op.add_column("org_payment_configs", sa.Column("bank_account_name", sa.String(255), nullable=True))
    op.add_column("org_payment_configs", sa.Column("bank_account_number", sa.String(50), nullable=True))
    op.add_column("org_payment_configs", sa.Column("bank_sort_code", sa.String(20), nullable=True))
    op.add_column("org_payment_configs", sa.Column("credit_limit", sa.Numeric(12, 2), nullable=True))
    op.add_column("org_payment_configs", sa.Column("credit_utilization_warning_pct", sa.Integer(), nullable=True))
    op.add_column("org_payment_configs", sa.Column("return_to_sender_fee", sa.Numeric(10, 2), nullable=True))

    # ── 1. Revert org_discount_configs ────────────────────────────────────────
    op.drop_index("ix_org_discount_configs_discount_type", table_name="org_discount_configs")
    op.drop_index("ix_org_discount_configs_service_tier_id", table_name="org_discount_configs")
    op.drop_index("ix_org_discount_configs_organization_id", table_name="org_discount_configs")
    op.drop_table("org_discount_configs")

    op.create_table(
        "org_discount_configs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("percentage_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("percentage_value", sa.Numeric(5, 2), nullable=True),
        sa.Column("percentage_valid_from", sa.Date(), nullable=True),
        sa.Column("percentage_valid_until", sa.Date(), nullable=True),
        sa.Column("fixed_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("fixed_value", sa.Numeric(10, 2), nullable=True),
        sa.Column("fixed_valid_from", sa.Date(), nullable=True),
        sa.Column("fixed_valid_until", sa.Date(), nullable=True),
        sa.Column("volume_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("volume_tiers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_org_discount_configs_organization_id", "org_discount_configs", ["organization_id"], unique=True)
