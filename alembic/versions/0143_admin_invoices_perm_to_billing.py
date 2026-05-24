"""Backfill admin + B2B permissions for the new resource matrix.

Three things happen in one pass:

1. Admin-side billing routes are unified onto ``Resource.BILLING``, so any
   existing ``user_permissions`` row owned by an ADMIN / SUPER_ADMIN with
   ``resource = 'INVOICES'`` is renamed to ``resource = 'BILLING'``.

2. Seed two admin matrix entries (upsert) for every ADMIN / SUPER_ADMIN user:
   - ``NOTIFICATIONS`` → ``WRITE`` (2) — newly enabled on the admin matrix.
   - ``QUICKBOOKS``    → ``NONE``  (0) — locked off until per-admin opt-in.

3. B2B-side: any ``user_permissions`` row owned by a ``CUSTOMER_B2B`` user
   with ``resource = 'REQUESTS'`` is renamed to ``resource = 'ORDERS'`` to
   match the new B2B portal naming.

The seed step upserts on ``uq_user_permissions_user_resource`` so users with
an existing row have their level overwritten and users without a row get a
fresh row inserted.

Revision ID: 0143_admin_invoices_to_billing
Revises: 0142_quickbooks_global_singleton
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0143_admin_invoices_to_billing"
down_revision: str | None = "0142_quickbooks_global_singleton"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Rename admin INVOICES rows to BILLING.
    op.execute(sa.text("""
        UPDATE user_permissions
           SET resource = 'BILLING'
         WHERE resource = 'INVOICES'
           AND user_id IN (
                 SELECT id FROM users
                  WHERE role IN ('ADMIN', 'SUPER_ADMIN')
               )
    """))

    # 2) NOTIFICATIONS → WRITE for every admin / super-admin.
    op.execute(sa.text("""
        INSERT INTO user_permissions (id, user_id, resource, level, created_at, updated_at, version)
        SELECT gen_random_uuid(), u.id, 'NOTIFICATIONS', 2, now(), now(), 1
          FROM users u
         WHERE u.role IN ('ADMIN', 'SUPER_ADMIN')
        ON CONFLICT ON CONSTRAINT uq_user_permissions_user_resource
        DO UPDATE SET level = EXCLUDED.level, updated_at = now()
    """))

    # 3) QUICKBOOKS → NONE for every admin / super-admin.
    op.execute(sa.text("""
        INSERT INTO user_permissions (id, user_id, resource, level, created_at, updated_at, version)
        SELECT gen_random_uuid(), u.id, 'QUICKBOOKS', 0, now(), now(), 1
          FROM users u
         WHERE u.role IN ('ADMIN', 'SUPER_ADMIN')
        ON CONFLICT ON CONSTRAINT uq_user_permissions_user_resource
        DO UPDATE SET level = EXCLUDED.level, updated_at = now()
    """))

    # 4) B2B clients: rename REQUESTS → ORDERS.
    op.execute(sa.text("""
        UPDATE user_permissions
           SET resource = 'ORDERS'
         WHERE resource = 'REQUESTS'
           AND user_id IN (
                 SELECT id FROM users
                  WHERE role = 'CUSTOMER_B2B'
               )
    """))


def downgrade() -> None:
    # Reverse the B2B ORDERS → REQUESTS rename.
    op.execute(sa.text("""
        UPDATE user_permissions
           SET resource = 'REQUESTS'
         WHERE resource = 'ORDERS'
           AND user_id IN (
                 SELECT id FROM users
                  WHERE role = 'CUSTOMER_B2B'
               )
    """))

    # Drop the seed rows; prior levels aren't preserved so we just delete them
    # and let callers fall back to the role-default matrix.
    op.execute(sa.text("""
        DELETE FROM user_permissions
         WHERE resource IN ('NOTIFICATIONS', 'QUICKBOOKS')
           AND user_id IN (
                 SELECT id FROM users
                  WHERE role IN ('ADMIN', 'SUPER_ADMIN')
               )
    """))

    # Reverse the admin INVOICES → BILLING rename.
    op.execute(sa.text("""
        UPDATE user_permissions
           SET resource = 'INVOICES'
         WHERE resource = 'BILLING'
           AND user_id IN (
                 SELECT id FROM users
                  WHERE role IN ('ADMIN', 'SUPER_ADMIN')
               )
    """))
