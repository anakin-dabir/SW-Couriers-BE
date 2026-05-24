"""Add ``order_drafts.total_amount`` to persist the Step-4 price-breakdown total.

The wizard's Payment Method step computes a live grand total via
``POST /orders/price-breakdown``. When the user hits Save as Draft we now
capture that figure on the draft itself so the drafts-list API can show the
order value without re-running pricing. The column is draft-only: it is
**not** copied to ``orders`` on submit; the order's price breakdown is
re-computed authoritatively at create time.

Revision ID: 0150_order_drafts_total_amount
Revises: 0149_billing_enhancements
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0150_order_drafts_total_amount"
down_revision: str | None = "0149_billing_enhancements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "order_drafts",
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("order_drafts", "total_amount")
