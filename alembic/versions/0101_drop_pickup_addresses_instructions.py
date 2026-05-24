"""drop instructions from pickup_addresses

Revision ID: 0101_drop_pickup_instructions
Revises: 0100_pickup_consolidation
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0101_drop_pickup_instructions"
down_revision: str | None = "0100_pickup_consolidation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("pickup_addresses", "instructions")


def downgrade() -> None:
    op.add_column(
        "pickup_addresses",
        sa.Column("instructions", sa.Text(), nullable=True),
    )
