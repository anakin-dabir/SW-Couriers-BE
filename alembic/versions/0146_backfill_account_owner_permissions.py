"""Backfill missing permission overrides for existing ACCOUNT_OWNER contacts.

Revision ID: 0146_backfill_acc_owner_perms
Revises: 0145_backfill_ocontact_owner
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0146_backfill_acc_owner_perms"
down_revision: str | None = "0145_backfill_ocontact_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Only the resources whose floor is STRICTLY higher than the CUSTOMER_B2B
# default need a ``user_permissions`` row. Resources where floor == default
# (ORDERS / CARD_PAYMENT / REQUEST_CREDIT / CONTACTS / DASHBOARD) are covered
# by the role default and don't need an override.
_OWNER_PERMISSION_DELTAS: tuple[tuple[str, int], ...] = (
    ("BILLING", 2),  # WRITE
    ("NOTIFICATIONS", 2),  # WRITE
    ("DOCUMENTS", 2),  # WRITE
    ("ORG_PROFILE", 2),  # WRITE
    ("AUDIT_LOG", 2),  # READ
)


def upgrade() -> None:
    for resource, level in _OWNER_PERMISSION_DELTAS:
        op.execute(sa.text("""
            INSERT INTO user_permissions (id, user_id, resource, level, created_at, updated_at, version)
            SELECT gen_random_uuid(), oc.user_id, :resource, :level, now(), now(), 1
              FROM org_contacts oc
             WHERE oc.contact_role = 'ACCOUNT_OWNER'
               AND oc.status != 'INACTIVE'
               AND oc.user_id IS NOT NULL
            ON CONFLICT ON CONSTRAINT uq_user_permissions_user_resource
            DO UPDATE SET level = GREATEST(user_permissions.level, EXCLUDED.level),
                          updated_at = now()
        """).bindparams(resource=resource, level=level))


def downgrade() -> None:
    # Not safely reversible: the upgrade may have promoted existing lower-level
    # rows via GREATEST. We can't tell those apart from rows we inserted from
    # scratch. Leaving as a no-op rather than dropping rows that may have been
    # legitimately created later.
    pass
