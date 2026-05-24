"""driver_country_state

Add country and state columns to drivers.

Revision ID: 0027_driver_country_state
Revises: 0026_org_discounts
Create Date: 2026-03-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_driver_country_state"
down_revision = "0026_org_discounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("drivers", sa.Column("country", sa.String(length=100), nullable=True))
    op.add_column("drivers", sa.Column("state", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("drivers", "state")
    op.drop_column("drivers", "country")
