"""org_on_hold_status

Adds ON_HOLD to the OrganizationStatus enum values.

Since the status column uses native_enum=False (VARCHAR), no PostgreSQL type
alteration is needed — this migration simply documents the new allowed value
so that alembic autogenerate stays in sync.

Revision ID: 0023_org_on_hold_status
Revises: 0022_org_notes_and_tags
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_org_on_hold_status"
down_revision = "0022_org_notes_and_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The status column is VARCHAR (native_enum=False), so no DDL change is
    # required to allow the new ON_HOLD value.  We do add a CHECK constraint
    # that matches the full allowed-value set so the DB enforces integrity.
    op.execute(
        """
        ALTER TABLE organizations
        DROP CONSTRAINT IF EXISTS ck_organizations_status;
        """
    )
    op.execute(
        """
        ALTER TABLE organizations
        ADD CONSTRAINT ck_organizations_status
        CHECK (status IN ('ACTIVE', 'ON_HOLD', 'SUSPENDED', 'INACTIVE'));
        """
    )


def downgrade() -> None:
    # Remove ON_HOLD from the constraint; fail if any row currently holds it.
    op.execute(
        """
        ALTER TABLE organizations
        DROP CONSTRAINT IF EXISTS ck_organizations_status;
        """
    )
    op.execute(
        """
        ALTER TABLE organizations
        ADD CONSTRAINT ck_organizations_status
        CHECK (status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED'));
        """
    )
