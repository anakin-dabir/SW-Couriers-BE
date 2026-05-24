"""Users: add password_changed_at, drop MFA columns.

Revision ID: 0004_users_refresh
Revises: 0003_hyper
Create Date: 2026-02-23

- Add password_changed_at (nullable) for 90-day rotation tracking.
- Drop mfa_enabled, mfa_secret, mfa_backup_codes (MFA removed from auth flow).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_users_refresh"
down_revision: str = "0003_hyper"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_column("users", "mfa_backup_codes")
    op.drop_column("users", "mfa_secret")
    op.drop_column("users", "mfa_enabled")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("users", sa.Column("mfa_secret", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column("mfa_backup_codes", postgresql.ARRAY(sa.String()), nullable=True),
    )
    op.drop_column("users", "password_changed_at")
