"""Add updated_at to account_statement_delivery_events.

Revision ID: 0153_account_statement_delivery_updated_at
Revises: 0152_seed_superfast_system_tier
Create Date: 2026-05-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0153_stmt_delivery_updated_at"
down_revision: str | None = "0152_seed_superfast_system_tier"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "account_statement_delivery_events",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("account_statement_delivery_events", "updated_at")
