"""Backfill driver profile photo keys into user avatar_url.

Existing driver profile photos were stored only on ``drivers.profile_photo_key``.
The application now mirrors that image key into ``users.avatar_url`` for the
linked DRIVER user so auth/me can return one signed avatar URL consistently.

Revision ID: 0144_backfill_driver_avatar_url
Revises: 0143_admin_invoices_to_billing
Create Date: 2026-05-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0144_backfill_driver_avatar_url"
down_revision: str | None = "0143_admin_invoices_to_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE users AS u
           SET avatar_url = d.profile_photo_key,
               updated_at = now()
          FROM drivers AS d
         WHERE d.user_id = u.id
           AND u.role = 'DRIVER'
           AND d.profile_photo_key IS NOT NULL
           AND d.profile_photo_key <> ''
           AND (u.avatar_url IS NULL OR u.avatar_url = '')
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        UPDATE users AS u
           SET avatar_url = NULL,
               updated_at = now()
          FROM drivers AS d
         WHERE d.user_id = u.id
           AND u.role = 'DRIVER'
           AND d.profile_photo_key IS NOT NULL
           AND u.avatar_url = d.profile_photo_key
    """))
