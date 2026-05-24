"""add billing payment dispute fields

Revision ID: 0115_billing_payment_disputes
Revises: 0114_order_pricing_config
Create Date: 2026-05-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0115_billing_payment_disputes"
down_revision: str | None = "0114_order_pricing_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "billing_payments",
        sa.Column("dispute_amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "billing_payments",
        sa.Column("dispute_fee", sa.Numeric(10, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "billing_payments",
        sa.Column("dispute_status", sa.String(length=50), nullable=True),
    )
    op.create_check_constraint(
        "ck_billing_payments_dispute_amount_non_negative",
        "billing_payments",
        "dispute_amount >= 0",
    )
    op.create_check_constraint(
        "ck_billing_payments_dispute_fee_non_negative",
        "billing_payments",
        "dispute_fee >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_billing_payments_dispute_amount_non_negative", "billing_payments", type_="check")
    op.drop_constraint("ck_billing_payments_dispute_fee_non_negative", "billing_payments", type_="check")
    op.drop_column("billing_payments", "dispute_status")
    op.drop_column("billing_payments", "dispute_fee")
    op.drop_column("billing_payments", "dispute_amount")
