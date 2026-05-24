"""org_phone

Adds phone column to the organizations table.
Editable by the org's ACCOUNT_OWNER via the B2B portal.

Revision ID: 0015_org_logo_phone
Revises: 0014_org_contacts_primary
Create Date: 2026-03-13

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_org_logo_phone"
down_revision: str | None = "0014_org_contacts_primary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("phone", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "phone")
