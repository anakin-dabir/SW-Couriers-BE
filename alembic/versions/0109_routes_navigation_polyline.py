"""Cache directions polyline and metadata on ``routes`` (per driver run).

``route_plans`` holds many ``routes``; navigation geometry is **per route** (ordered
``route_stops``). ``navigation_fingerprint`` hashes ordered route-stop ids so stale
polylines can be hidden after replan.

**Population (application code, not this migration):** after a route is built, or whenever
``route_stops`` are added/reordered/removed, a planner step or **async job** should call the
directions provider (Google/OSRM/etc.), then set ``navigation_encoded_polyline``,
``navigation_meta`` (provider, computed_at, distances, …), and ``navigation_fingerprint`` =
``compute_route_navigation_fingerprint`` from ``app.modules.planning.route_navigation``. Do not
call the provider from ``GET …/active-driving-map`` (read-only, uses cached values).

**Parent:** ``0108_route_stops_stop_flow_type``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0109_routes_navigation_polyline"
down_revision: str | None = "0108_route_stops_stop_flow_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("routes", sa.Column("navigation_encoded_polyline", sa.Text(), nullable=True))
    op.add_column(
        "routes",
        sa.Column("navigation_meta", JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("routes", sa.Column("navigation_fingerprint", sa.String(length=64), nullable=True))
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_route_events_route_ping_occurred
        ON route_events (route_id, occurred_at DESC)
        WHERE event_type = 'LOCATION_PING' AND lat IS NOT NULL AND lng IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_route_events_route_ping_occurred")
    op.drop_column("routes", "navigation_fingerprint")
    op.drop_column("routes", "navigation_meta")
    op.drop_column("routes", "navigation_encoded_polyline")
