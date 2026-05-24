"""drop orders payment_status

Revision ID: 0116_drop_orders_payment_status
Revises: 0115_billing_payment_disputes
Create Date: 2026-05-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0116_drop_orders_payment_status"
down_revision: str | None = "0115_billing_payment_disputes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("orders", "payment_status")


def downgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("payment_status", sa.String(length=30), nullable=False, server_default="pending"),
    )
