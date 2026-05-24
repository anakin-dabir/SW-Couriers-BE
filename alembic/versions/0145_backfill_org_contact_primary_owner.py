"""Backfill ``org_contacts.is_primary`` for ACCOUNT_OWNER contacts.

The "main point of contact" flag (``is_primary``) was added later than the
``contact_role`` field, so older organisations have at least one
``ACCOUNT_OWNER`` contact but no row marked ``is_primary = TRUE``. The
owner-resolution code now prefers a primary ACCOUNT_OWNER, falling back to
deterministic ordering when none is set; this migration backfills the flag
so the preferred path is taken for legacy orgs.

For every organisation that does NOT already have an ``is_primary = TRUE``
contact, the earliest-created active (non-INACTIVE) ``ACCOUNT_OWNER`` row is
flipped to ``is_primary = TRUE``. Organisations that already have a primary
contact — even on a non-owner row — are left alone, since that flag was set
deliberately and shouldn't be overwritten.

Revision ID: 0145_backfill_orcontac_owner
Revises: 0144_backfill_driver_avatar_url
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0145_backfill_ocontact_owner"
down_revision: str | None = "0144_backfill_driver_avatar_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("""
        WITH first_owner AS (
            SELECT DISTINCT ON (oc.organization_id) oc.id
              FROM org_contacts oc
             WHERE oc.contact_role = 'ACCOUNT_OWNER'
               AND oc.status != 'INACTIVE'
               AND NOT EXISTS (
                     SELECT 1 FROM org_contacts existing
                      WHERE existing.organization_id = oc.organization_id
                        AND existing.is_primary = TRUE
                   )
             ORDER BY oc.organization_id, oc.created_at ASC, oc.id ASC
        )
        UPDATE org_contacts
           SET is_primary = TRUE,
               updated_at = now()
         WHERE id IN (SELECT id FROM first_owner)
    """))


def downgrade() -> None:
    # Not safely reversible: the upgrade can't distinguish rows it flipped from
    # rows that were primary before. Leaving as a no-op rather than blindly
    # clearing the flag on every owner.
    pass
