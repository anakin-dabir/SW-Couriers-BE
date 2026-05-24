"""Make driver profile fields nullable for drafts.

Allows saving driver drafts with partial data by relaxing NOT NULL constraints
on selected profile columns in drivers table.

Revision ID: 0030_driver_nullable
Revises: 0029_driver_drafts
Create Date: 2026-03-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# IMPORTANT: alembic_version.version_num is VARCHAR(32) in this project.
# Keep revision ids <= 32 chars to avoid truncation errors.
revision = "0030_driver_nullable"
down_revision = "0029_driver_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Address / profile fields become nullable to support drafts.
    op.alter_column("drivers", "address_line1", existing_type=sa.String(length=255), nullable=True)
    op.alter_column("drivers", "city", existing_type=sa.String(length=100), nullable=True)
    op.alter_column("drivers", "postcode", existing_type=sa.String(length=20), nullable=True)

    # Draftable structured fields
    op.alter_column("drivers", "driver_type", existing_type=sa.String(length=20), nullable=True)
    op.alter_column(
        "drivers",
        "capacities",
        existing_type=postgresql.ARRAY(sa.String(length=20)),
        nullable=True,
        existing_server_default=sa.text("ARRAY['VAN']::varchar[]"),
        server_default=None,
    )

    # Optional for drafts; enforced on submit.
    op.alter_column("drivers", "max_stops", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # Revert to prior NOT NULL constraints.
    op.alter_column("drivers", "max_stops", existing_type=sa.Integer(), nullable=False)
    op.alter_column(
        "drivers",
        "capacities",
        existing_type=postgresql.ARRAY(sa.String(length=20)),
        nullable=False,
        server_default=sa.text("ARRAY['VAN']::varchar[]"),
    )
    op.alter_column("drivers", "driver_type", existing_type=sa.String(length=20), nullable=False)

    op.alter_column("drivers", "postcode", existing_type=sa.String(length=20), nullable=False)
    op.alter_column("drivers", "city", existing_type=sa.String(length=100), nullable=False)
    op.alter_column("drivers", "address_line1", existing_type=sa.String(length=255), nullable=False)

