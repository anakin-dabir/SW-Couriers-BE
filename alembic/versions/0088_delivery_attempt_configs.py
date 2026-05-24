"""Create delivery_attempt_configs table — global delivery & return attempt charge settings.

Revision ID: 0088_delivery_attempt_configs
Revises: 0087_service_tier_scope
Create Date: 2026-04-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0088_delivery_attempt_configs"
down_revision: str | None = "0087_service_tier_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_attempt_configs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("max_delivery_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "delivery_attempt_fees",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("max_return_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "return_attempt_fees",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("delivery_attempt_configs")
