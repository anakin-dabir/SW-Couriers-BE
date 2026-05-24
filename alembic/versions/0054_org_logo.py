"""org_logo

Adds logo_cf_image_id column to the organizations table.
Stores the Cloudflare Images ID for the organisation's profile image.
Signed CDN URLs are generated on-demand; the raw ID is never exposed in API responses.

Revision ID: 0054_org_logo
Revises: 0053_org_notes_refactor
Create Date: 2026-04-03

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0054_org_logo"
down_revision: str | None = "0053_org_notes_refactor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("logo_cf_image_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "logo_cf_image_id")
