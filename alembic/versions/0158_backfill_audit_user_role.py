"""Backfill audit_log user_role column with actual user roles.

Revision ID: 0158_backfill_audit_user_role
Revises: 0157_driver_shift_origin
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0158_backfill_audit_user_role"
down_revision: str | None = "0157_driver_shift_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE audit_log as al
        SET user_role = u.role
        FROM users as u
        WHERE al.user_id = u.id AND al.user_role IS NULL AND u.role IS NOT NULL
        """
    )


def downgrade() -> None:
    pass