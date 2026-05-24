"""add order pricing config snapshot

Revision ID: 0114_order_pricing_config
Revises: 0113_backfill_owner_audit
Create Date: 2026-05-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0114_order_pricing_config"
down_revision: str | None = "0113_backfill_owner_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("pricing_config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "pricing_config_snapshot")
