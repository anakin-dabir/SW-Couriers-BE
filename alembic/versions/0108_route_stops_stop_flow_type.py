"""Add ``route_stops.stop_flow_type`` (PICKUP / DELIVERY / RETURN).

Per-stop operational leg on the route. Existing rows backfill to ``DELIVERY``.
Independent of ``routes.route_type`` (route-level planning category).

**Parent:** ``0107_stop_notes_package_ids``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0108_route_stops_stop_flow_type"
down_revision: str | None = "0107_stop_notes_package_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "route_stops",
        sa.Column(
            "stop_flow_type",
            sa.String(length=20),
            nullable=False,
            server_default="DELIVERY",
        ),
    )
    op.create_index("ix_route_stops_stop_flow_type", "route_stops", ["stop_flow_type"], unique=False)
    op.alter_column("route_stops", "stop_flow_type", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_route_stops_stop_flow_type", table_name="route_stops")
    op.drop_column("route_stops", "stop_flow_type")
