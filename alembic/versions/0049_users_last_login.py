"""users_last_login

Add last_login timestamp to users table (set on every successful login).
Also adds SUPER_ADMIN as a valid value for the users.role column.

Revision ID: 0049_users_last_login
Revises: 0048_users_title_position
Create Date: 2026-04-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049_users_last_login"
down_revision = "0048_users_title_position"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "last_login")
