"""Backfill B2B contact user status from INACTIVE to PENDING_VERIFICATION.

Revision ID: 0147_b2b_user_status_pending
Revises: 0146_backfill_acc_owner_perms
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0147_b2b_user_status_pending"
down_revision: str | None = "0146_backfill_acc_owner_perms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE users u
           SET status = 'PENDING_VERIFICATION',
               updated_at = now()
         WHERE u.status = 'INACTIVE'
           AND (
                 u.organization_id IS NOT NULL
              OR EXISTS (
                   SELECT 1 FROM org_contacts oc WHERE oc.user_id = u.id
                 )
               )
    """))


def downgrade() -> None:
    # Not safely reversible: we can't tell which PENDING_VERIFICATION rows we
    # promoted from INACTIVE versus rows that were already PENDING_VERIFICATION
    # before. Leaving as a no-op rather than blindly demoting.
    pass
