"""Add access_jti column to refresh_tokens.

Revision ID: 0010_access_jti
Revises: 0009_user_permissions
Create Date: 2026-03-10

Stores the paired access token's JTI so logout can blacklist it
without requiring the access token itself.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_access_jti"
down_revision: str = "0009_user_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("refresh_tokens", sa.Column("access_jti", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("refresh_tokens", "access_jti")
