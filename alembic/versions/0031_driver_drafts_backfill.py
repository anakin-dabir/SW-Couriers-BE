"""Backfill driver_drafts for existing DRAFT drivers.

Creates missing pivot rows for existing drivers where account_status='DRAFT'.
Lets the DB assign draft_id (DF-NNN) via draft_code_seq default.

Revision ID: 0031_draft_backfill
Revises: 0030_driver_nullable
Create Date: 2026-03-26
"""

from __future__ import annotations

from alembic import op

revision = "0031_draft_backfill"
down_revision = "0030_driver_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # gen_random_uuid() comes from pgcrypto.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        """
        INSERT INTO driver_drafts (id, driver_id, created_by, created_at, updated_at)
        SELECT gen_random_uuid(), d.id, NULL, now(), now()
        FROM drivers d
        WHERE d.account_status = 'DRAFT'
          AND NOT EXISTS (
            SELECT 1 FROM driver_drafts dd WHERE dd.driver_id = d.id
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM driver_drafts dd
        WHERE dd.created_by IS NULL
          AND EXISTS (
            SELECT 1 FROM drivers d
            WHERE d.id = dd.driver_id AND d.account_status = 'DRAFT'
          )
        """
    )

