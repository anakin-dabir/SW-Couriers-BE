"""org_contacts_primary_flag

Adds is_primary boolean column to org_contacts table.
Exactly one active contact per org should be flagged as primary.

Revision ID: 0014_org_contacts_primary
Revises: 0013_settings_pricing
Create Date: 2026-03-13

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_org_contacts_primary"
down_revision: str | None = "0013_settings_pricing_tiers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "org_contacts",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("org_contacts", "is_primary")
