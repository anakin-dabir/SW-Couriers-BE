"""route_stops.order_id for pickup-flow stops

Adds a nullable ``route_stops.order_id`` FK to ``orders.id``. PICKUP-flow route stops reference
the Order they're collecting (which carries ``pickup_address_id`` + ``packages``), so the route
stop no longer needs a synthetic ``delivery_stops`` row to model a pickup leg.

Existing rows are unaffected (column is nullable; default null).

Revision ID: 0130_route_stops_order_id
Revises: 0129_route_stop_history
Create Date: 2026-05-14 12:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0130_route_stops_order_id"
down_revision: str | None = "0129_route_stop_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.add_column(
        "route_stops",
        sa.Column("order_id", sa.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_route_stops_order_id",
        source_table="route_stops",
        referent_table="orders",
        local_cols=["order_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_route_stops_order_id",
        table_name="route_stops",
        columns=["order_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_route_stops_order_id", table_name="route_stops")
    op.drop_constraint("fk_route_stops_order_id", "route_stops", type_="foreignkey")
    op.drop_column("route_stops", "order_id")
