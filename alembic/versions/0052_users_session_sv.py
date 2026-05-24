"""Add users.session_sv for session generation.

Adds an integer generation/version used to invalidate all sessions (logout-all)
in a race-safe way. New access tokens embed `sv` and requests can validate it.

Revision ID: 0052_users_session_sv
Revises: 0051_sessions_table
Create Date: 2026-04-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_users_session_sv"
down_revision: str | None = "0051_sessions_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("session_sv", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("users", "session_sv")

