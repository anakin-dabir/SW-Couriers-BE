"""org_vat_number_nullable

Revision ID: 0065_org_vat_number_nullable
Revises: 0064_user_centric_views
Create Date: 2026-04-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0065_org_vat_number_nullable"
down_revision: Union[str, None] = "0064_user_centric_views"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "organizations",
        "vat_number",
        existing_type=sa.String(50),
        nullable=True,
    )


def downgrade() -> None:
    # Set any NULLs to empty string before restoring NOT NULL
    op.execute("UPDATE organizations SET vat_number = '' WHERE vat_number IS NULL")
    op.alter_column(
        "organizations",
        "vat_number",
        existing_type=sa.String(50),
        nullable=False,
    )
