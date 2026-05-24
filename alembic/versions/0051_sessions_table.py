"""Add sessions table and link refresh tokens.

Creates a first-class `sessions` table to model device sessions and links
`refresh_tokens` to it via a nullable FK column.

Revision ID: 0051_sessions_table
Revises: 0050_vm_garage.py
Create Date: 2026-04-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051_sessions_table"
down_revision: str | None = "0050_vm_garage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── sessions ──────────────────────────────────────────────────────────────
    op.create_table(
        "sessions",
        sa.Column("session_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("inactivity_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_revoked", "sessions", ["revoked"])
    op.create_index("ix_sessions_user_id_revoked_last_seen_at", "sessions", ["user_id", "revoked", "last_seen_at"])
    op.create_index("ix_sessions_session_id_revoked", "sessions", ["session_id", "revoked"])

    # ── refresh_tokens.session_id (nullable FK) ───────────────────────────────
    op.add_column("refresh_tokens", sa.Column("session_id", postgresql.UUID(as_uuid=False), nullable=True))
    op.create_foreign_key(
        "fk_refresh_tokens_session_id_sessions",
        "refresh_tokens",
        "sessions",
        ["session_id"],
        ["session_id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_refresh_tokens_session_id", "refresh_tokens", ["session_id"])

    # Index for mapping access token -> refresh row when blacklisting
    op.create_index("ix_refresh_tokens_access_jti", "refresh_tokens", ["access_jti"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_access_jti", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_session_id", table_name="refresh_tokens")
    op.drop_constraint("fk_refresh_tokens_session_id_sessions", "refresh_tokens", type_="foreignkey")
    op.drop_column("refresh_tokens", "session_id")

    op.drop_index("ix_sessions_session_id_revoked", table_name="sessions")
    op.drop_index("ix_sessions_user_id_revoked_last_seen_at", table_name="sessions")
    op.drop_index("ix_sessions_revoked", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")

