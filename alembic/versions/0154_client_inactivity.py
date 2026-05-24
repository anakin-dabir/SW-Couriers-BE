"""Client inactivity config and user inactivity tracking.

Revision ID: 0154_client_inactivity
Revises: 0153_stmt_delivery_updated_at
Create Date: 2026-05-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0154_client_inactivity"
down_revision: str | None = "0153_stmt_delivery_updated_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "client_inactivity_configs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("inactive_after_days", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column("users", sa.Column("inactive_reason", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("inactivated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "inactivated_at")
    op.drop_column("users", "inactive_reason")
    op.drop_table("client_inactivity_configs")
