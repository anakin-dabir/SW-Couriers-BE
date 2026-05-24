"""org_pickup_addresses

Create org_pickup_addresses table.
Each organisation can have multiple pickup addresses; exactly one is marked default.
latitude/longitude are optional (set when pin-dropped on a map).

Revision ID: 0046_org_pickup_addresses
Revises: 0045_org_trading_address
Create Date: 2026-04-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0046_org_pickup_addresses"
down_revision = "0045_org_trading_address"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_pickup_addresses",
        sa.Column("id", UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("organization_id", UUID(as_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        # Address
        sa.Column("address_line_1", sa.String(255), nullable=False),
        sa.Column("address_line_2", sa.String(255), nullable=True),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("state", sa.String(100), nullable=True),
        sa.Column("postcode", sa.String(20), nullable=False),
        sa.Column("country", sa.String(100), nullable=False, server_default="United Kingdom"),
        # Geo coordinates (optional)
        sa.Column("latitude", sa.Float, nullable=True),
        sa.Column("longitude", sa.Float, nullable=True),
        # Default flag
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        # Timestamps + version (BaseModel)
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_index("ix_org_pickup_addresses_is_default", "org_pickup_addresses", ["is_default"])


def downgrade() -> None:
    op.drop_index("ix_org_pickup_addresses_is_default", table_name="org_pickup_addresses")
    op.drop_table("org_pickup_addresses")
