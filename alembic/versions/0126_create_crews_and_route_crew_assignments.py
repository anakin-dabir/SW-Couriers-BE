"""create crews and route_crew_assignments

Revision ID: 0126_create_crews_rca (max 32 chars for alembic_version.version_num).
Revises: 0125_vehicle_service_alert
Create Date: 2026-05-14 09:55:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0126_create_crews_rca"
down_revision: str | None = "0125_vehicle_service_alert"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = sa.UUID(as_uuid=False)


def upgrade() -> None:
    op.create_table(
        "crews",
        sa.Column("id", _UUID, primary_key=True, nullable=False),
        sa.Column(
            "driver_id",
            _UUID,
            sa.ForeignKey("users.id", name="fk_crews_driver_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "vehicle_id",
            _UUID,
            sa.ForeignKey("vehicles.id", name="fk_crews_vehicle_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "started_by_id",
            _UUID,
            sa.ForeignKey("users.id", name="fk_crews_started_by_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "ended_by_id",
            _UUID,
            sa.ForeignKey("users.id", name="fk_crews_ended_by_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("end_reason", sa.String(length=40), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_crews_time_window",
        ),
    )
    op.create_index("ix_crews_driver_id", "crews", ["driver_id"])
    op.create_index("ix_crews_vehicle_id", "crews", ["vehicle_id"])
    op.create_index("ix_crews_ended_at", "crews", ["ended_at"])
    op.create_index(
        "uq_crews_open_driver",
        "crews",
        ["driver_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL AND driver_id IS NOT NULL"),
    )
    op.create_index(
        "uq_crews_open_vehicle",
        "crews",
        ["vehicle_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL AND vehicle_id IS NOT NULL"),
    )

    op.create_table(
        "route_crew_assignments",
        sa.Column("id", _UUID, primary_key=True, nullable=False),
        sa.Column(
            "route_id",
            _UUID,
            sa.ForeignKey("routes.id", name="fk_rca_route_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "crew_id",
            _UUID,
            sa.ForeignKey("crews.id", name="fk_rca_crew_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("unassigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "assigned_by_id",
            _UUID,
            sa.ForeignKey("users.id", name="fk_rca_assigned_by_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "unassigned_by_id",
            _UUID,
            sa.ForeignKey("users.id", name="fk_rca_unassigned_by_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "unassigned_at IS NULL OR unassigned_at >= assigned_at",
            name="ck_rca_time_window",
        ),
    )
    op.create_index(
        "ix_route_crew_assignments_route_id",
        "route_crew_assignments",
        ["route_id"],
    )
    op.create_index(
        "ix_route_crew_assignments_crew_id",
        "route_crew_assignments",
        ["crew_id"],
    )
    op.create_index("ix_rca_unassigned_at", "route_crew_assignments", ["unassigned_at"])
    op.create_index(
        "uq_rca_open_per_route",
        "route_crew_assignments",
        ["route_id"],
        unique=True,
        postgresql_where=sa.text("unassigned_at IS NULL"),
    )
    op.create_index(
        "uq_rca_open_per_crew",
        "route_crew_assignments",
        ["crew_id"],
        unique=True,
        postgresql_where=sa.text("unassigned_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_rca_open_per_crew", table_name="route_crew_assignments")
    op.drop_index("uq_rca_open_per_route", table_name="route_crew_assignments")
    op.drop_index("ix_rca_unassigned_at", table_name="route_crew_assignments")
    op.drop_index("ix_route_crew_assignments_crew_id", table_name="route_crew_assignments")
    op.drop_index("ix_route_crew_assignments_route_id", table_name="route_crew_assignments")
    op.drop_table("route_crew_assignments")

    op.drop_index("uq_crews_open_vehicle", table_name="crews")
    op.drop_index("uq_crews_open_driver", table_name="crews")
    op.drop_index("ix_crews_ended_at", table_name="crews")
    op.drop_index("ix_crews_vehicle_id", table_name="crews")
    op.drop_index("ix_crews_driver_id", table_name="crews")
    op.drop_table("crews")
