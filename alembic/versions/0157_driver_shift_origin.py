"""Add origin column to driver_shifts (weekly template vs manual).

Revision ID: 0157_driver_shift_origin
Revises: 0156_bt_webhook_idempotency
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0157_driver_shift_origin"
down_revision: str | None = "0156_bt_webhook_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "driver_shifts",
        sa.Column("origin", sa.String(length=32), nullable=False, server_default="MANUAL"),
    )
    op.alter_column("driver_shifts", "origin", server_default=None)


def downgrade() -> None:
    op.drop_column("driver_shifts", "origin")
