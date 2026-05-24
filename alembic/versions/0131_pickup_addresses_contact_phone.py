"""Add optional contact_phone to pickup_addresses.

Revision ID: 0131_pickup_contact_phone
Revises: 0130_route_stops_order_id
Create Date: 2026-05-13

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0131_pickup_contact_phone"
down_revision: Union[str, None] = "0130_route_stops_order_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pickup_addresses",
        sa.Column("contact_phone", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pickup_addresses", "contact_phone")
