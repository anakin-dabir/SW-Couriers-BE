"""add requested_pickup_date to orders

Revision ID: 0103_orders_pickup_date
Revises: 0102_drop_card_billing_address
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0103_orders_pickup_date"
down_revision: str | None = "0102_drop_card_billing_address"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("requested_pickup_date", sa.Date(), nullable=True))
    op.create_index("ix_orders_requested_pickup_date", "orders", ["requested_pickup_date"])


def downgrade() -> None:
    op.drop_index("ix_orders_requested_pickup_date", table_name="orders")
    op.drop_column("orders", "requested_pickup_date")
