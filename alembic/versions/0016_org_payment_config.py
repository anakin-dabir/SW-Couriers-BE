"""org_payment_config

Creates the org_payment_configs table: one-to-one with organizations.
Stores payment model (card/bank_transfer/credit_account), billing schedule,
bank details, credit settings, VAT configuration, and delivery reattempt charges.

Revision ID: 0016_org_payment_config
Revises: 9f1c2a3b4d5e
Create Date: 2026-03-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0016_org_payment_config"
down_revision: str | None = "9f1c2a3b4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_payment_configs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        # One-to-one FK to organizations (UNIQUE enforces the 1-1 constraint)
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        # ── Payment model ─────────────────────────────────────────────────────
        sa.Column("payment_model", sa.String(30), nullable=False),
        # ── Billing schedule ──────────────────────────────────────────────────
        sa.Column("billing_schedule", sa.String(30), nullable=False),
        sa.Column("billing_day_of_month", sa.Integer(), nullable=True),  # FIXED_MONTHLY_DATE
        sa.Column("billing_days_after_order", sa.Integer(), nullable=True),  # DAYS_AFTER_ORDER
        # ── Bank details (BANK_TRANSFER only) ─────────────────────────────────
        sa.Column("bank_account_name", sa.String(255), nullable=True),
        sa.Column("bank_account_number", sa.String(50), nullable=True),
        sa.Column("bank_sort_code", sa.String(20), nullable=True),
        # ── Credit settings (CREDIT_ACCOUNT only) ─────────────────────────────
        sa.Column("credit_limit", sa.Numeric(12, 2), nullable=True),
        sa.Column("credit_utilization_warning_pct", sa.Integer(), nullable=True),
        # ── VAT ───────────────────────────────────────────────────────────────
        sa.Column("vat_rate", sa.String(20), nullable=False, server_default="STANDARD_20"),
        sa.Column("vat_treatment", sa.String(20), nullable=False, server_default="UK"),
        # ── Delivery reattempt charges ────────────────────────────────────────
        sa.Column("max_delivery_attempts", sa.Integer(), nullable=False, server_default="3"),
        # [{"attempt": 1, "fee": "1.00"}, ...]
        sa.Column("delivery_attempt_fees", JSONB(), nullable=True),
        sa.Column("return_to_sender_fee", sa.Numeric(10, 2), nullable=True),
        # ── Standard audit columns ────────────────────────────────────────────
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_index("ix_org_payment_configs_organization_id", "org_payment_configs", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_org_payment_configs_organization_id", table_name="org_payment_configs")
    op.drop_table("org_payment_configs")
