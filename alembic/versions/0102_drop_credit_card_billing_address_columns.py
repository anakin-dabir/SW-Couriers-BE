"""drop billing address columns from credit_cards

Revision ID: 0102_drop_card_billing_address
Revises: 0101_drop_pickup_instructions
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0102_drop_card_billing_address"
down_revision: str | None = "0101_drop_pickup_instructions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("credit_cards", "billing_postcode")
    op.drop_column("credit_cards", "billing_county")
    op.drop_column("credit_cards", "billing_city")
    op.drop_column("credit_cards", "billing_line2")
    op.drop_column("credit_cards", "billing_line1")
    op.drop_column("credit_cards", "billing_building")


def downgrade() -> None:
    op.add_column("credit_cards", sa.Column("billing_building", sa.String(255), nullable=True))
    op.add_column("credit_cards", sa.Column("billing_line1", sa.String(255), nullable=True))
    op.add_column("credit_cards", sa.Column("billing_line2", sa.String(255), nullable=True))
    op.add_column("credit_cards", sa.Column("billing_city", sa.String(100), nullable=True))
    op.add_column("credit_cards", sa.Column("billing_county", sa.String(100), nullable=True))
    op.add_column("credit_cards", sa.Column("billing_postcode", sa.String(20), nullable=True))
