"""org_trading_address

Add optional trading address columns to the organizations table.
When null, the frontend treats the trading address as same-as-registered.

Revision ID: 0045_org_trading_address
Revises: 0044_org_account_manager
Create Date: 2026-04-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045_org_trading_address"
down_revision = "0044_org_account_manager"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("trading_address_line_1", sa.String(255), nullable=True))
    op.add_column("organizations", sa.Column("trading_address_line_2", sa.String(255), nullable=True))
    op.add_column("organizations", sa.Column("trading_address_city", sa.String(100), nullable=True))
    op.add_column("organizations", sa.Column("trading_address_state", sa.String(100), nullable=True))
    op.add_column("organizations", sa.Column("trading_address_postcode", sa.String(20), nullable=True))
    op.add_column("organizations", sa.Column("trading_address_country", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "trading_address_country")
    op.drop_column("organizations", "trading_address_postcode")
    op.drop_column("organizations", "trading_address_state")
    op.drop_column("organizations", "trading_address_city")
    op.drop_column("organizations", "trading_address_line_2")
    op.drop_column("organizations", "trading_address_line_1")
