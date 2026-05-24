"""route_stops traveled geometry columns

Revision ID: 0129_route_stop_history
Revises: 0128_routes_null_plan
Create Date: 2026-05-14 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0129_route_stop_history"
down_revision: str | None = "0128_routes_null_plan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("route_stops", sa.Column("traveled_encoded_polyline", sa.Text(), nullable=True))
    op.add_column("route_stops", sa.Column("traveled_distance_m", sa.Integer(), nullable=True))
    op.add_column("route_stops", sa.Column("traveled_duration_s", sa.Integer(), nullable=True))
    op.add_column("route_stops", sa.Column("traveled_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("route_stops", sa.Column("traveled_ended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "route_stops",
        sa.Column("traveled_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("route_stops", "traveled_meta")
    op.drop_column("route_stops", "traveled_ended_at")
    op.drop_column("route_stops", "traveled_started_at")
    op.drop_column("route_stops", "traveled_duration_s")
    op.drop_column("route_stops", "traveled_distance_m")
    op.drop_column("route_stops", "traveled_encoded_polyline")
