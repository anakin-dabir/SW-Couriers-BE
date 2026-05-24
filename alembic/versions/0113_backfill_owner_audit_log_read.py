"""backfill account owner audit-log read permission

Revision ID: 0113_backfill_owner_audit
Revises: 0112_credit_note_update
Create Date: 2026-05-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0113_backfill_owner_audit"
down_revision: str | None = "0112_credit_note_update"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Backfill existing ACCOUNT_OWNER contacts so they can self-serve org audit APIs.
    # Idempotent and safe to re-run:
    # - inserts missing AUDIT_LOG override as READ (1)
    # - upgrades existing lower levels to READ
    # - preserves WRITE (2) if already granted
    op.execute(
        sa.text(
            """
            INSERT INTO user_permissions (
                id,
                user_id,
                resource,
                level,
                granted_by,
                created_at,
                updated_at,
                version
            )
            SELECT
                gen_random_uuid(),
                oc.user_id,
                'AUDIT_LOG',
                1,
                NULL,
                NOW(),
                NOW(),
                1
            FROM org_contacts oc
            JOIN users u ON u.id = oc.user_id
            WHERE oc.user_id IS NOT NULL
              AND oc.contact_role = 'ACCOUNT_OWNER'
              AND u.role = 'CUSTOMER_B2B'
            ON CONFLICT (user_id, resource) DO UPDATE
            SET
                level = GREATEST(user_permissions.level, EXCLUDED.level),
                updated_at = NOW(),
                version = user_permissions.version + 1
            WHERE user_permissions.level < EXCLUDED.level
            """
        )
    )


def downgrade() -> None:
    # No-op by design: this is a one-way data backfill and removing rows could
    # unintentionally strip legitimate permission overrides.
    pass

