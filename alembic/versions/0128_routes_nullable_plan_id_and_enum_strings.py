"""routes nullable plan_id, normalize planning strings, varchar to sa.Enum

Revision ID: 0128_routes_null_plan (max 32 chars for alembic_version.version_num).
Revises: 0127_credit_notes_qb_sync
Create Date: 2026-05-14 11:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0128_routes_null_plan"
down_revision: str | None = "0127_credit_notes_qb_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE route_plans SET status = upper(trim(status)) WHERE status IS NOT NULL"))
    op.execute(sa.text("UPDATE routes SET status = upper(trim(status)) WHERE status IS NOT NULL"))
    op.execute(sa.text("UPDATE routes SET route_type = upper(trim(route_type)) WHERE route_type IS NOT NULL"))
    op.execute(sa.text("UPDATE route_stops SET status = upper(trim(status)) WHERE status IS NOT NULL"))
    op.execute(
        sa.text(
            "UPDATE route_stops SET assignment_source = upper(trim(assignment_source)) "
            "WHERE assignment_source IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE route_stops SET stop_flow_type = upper(trim(stop_flow_type)) "
            "WHERE stop_flow_type IS NOT NULL"
        )
    )

    op.alter_column(
        "route_plans",
        "status",
        existing_type=sa.VARCHAR(length=30),
        type_=sa.Enum(
            "DRAFT",
            "OPTIMIZING",
            "READY",
            "LOCKED",
            "ACTIVE",
            "COMPLETED",
            name="routeplanstatus",
            native_enum=False,
        ),
        existing_nullable=False,
    )
    op.alter_column(
        "route_stops",
        "assignment_source",
        existing_type=sa.VARCHAR(length=20),
        type_=sa.Enum("INCREMENTAL", "OPTIMIZER", "MANUAL", name="stopassignmentsource", native_enum=False),
        existing_nullable=False,
    )
    op.alter_column(
        "route_stops",
        "status",
        existing_type=sa.VARCHAR(length=20),
        type_=sa.Enum(
            "PENDING",
            "NEEDS_REVIEW",
            "READY",
            "ASSIGNED",
            "EN_ROUTE",
            "ARRIVED",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
            name="routestopstatus",
            native_enum=False,
        ),
        existing_nullable=False,
    )
    op.alter_column(
        "route_stops",
        "stop_flow_type",
        existing_type=sa.VARCHAR(length=20),
        type_=sa.Enum("PICKUP", "DELIVERY", "RETURN", name="routestopflowtype", native_enum=False),
        existing_nullable=False,
    )
    op.alter_column(
        "routes",
        "route_type",
        existing_type=sa.VARCHAR(length=20),
        type_=sa.Enum("PICKUP", "DELIVERY", name="routetype", native_enum=False),
        existing_nullable=False,
        existing_server_default=sa.text("'DELIVERY'::character varying"),
    )
    op.alter_column(
        "routes",
        "status",
        existing_type=sa.VARCHAR(length=20),
        type_=sa.Enum("DRAFT", "ASSIGNED", "ACTIVE", "COMPLETED", name="routestatus", native_enum=False),
        existing_nullable=False,
    )

    op.alter_column(
        "routes",
        "plan_id",
        existing_type=sa.UUID(as_uuid=False),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "routes",
        "plan_id",
        existing_type=sa.UUID(as_uuid=False),
        nullable=False,
    )
    op.alter_column(
        "routes",
        "status",
        existing_type=sa.Enum("DRAFT", "ASSIGNED", "ACTIVE", "COMPLETED", name="routestatus", native_enum=False),
        type_=sa.VARCHAR(length=20),
        existing_nullable=False,
    )
    op.alter_column(
        "routes",
        "route_type",
        existing_type=sa.Enum("PICKUP", "DELIVERY", name="routetype", native_enum=False),
        type_=sa.VARCHAR(length=20),
        existing_nullable=False,
        existing_server_default=sa.text("'DELIVERY'::character varying"),
    )
    op.alter_column(
        "route_stops",
        "stop_flow_type",
        existing_type=sa.Enum("PICKUP", "DELIVERY", "RETURN", name="routestopflowtype", native_enum=False),
        type_=sa.VARCHAR(length=20),
        existing_nullable=False,
    )
    op.alter_column(
        "route_stops",
        "status",
        existing_type=sa.Enum(
            "PENDING",
            "NEEDS_REVIEW",
            "READY",
            "ASSIGNED",
            "EN_ROUTE",
            "ARRIVED",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
            name="routestopstatus",
            native_enum=False,
        ),
        type_=sa.VARCHAR(length=20),
        existing_nullable=False,
    )
    op.alter_column(
        "route_stops",
        "assignment_source",
        existing_type=sa.Enum("INCREMENTAL", "OPTIMIZER", "MANUAL", name="stopassignmentsource", native_enum=False),
        type_=sa.VARCHAR(length=20),
        existing_nullable=False,
    )
    op.alter_column(
        "route_plans",
        "status",
        existing_type=sa.Enum(
            "DRAFT",
            "OPTIMIZING",
            "READY",
            "LOCKED",
            "ACTIVE",
            "COMPLETED",
            name="routeplanstatus",
            native_enum=False,
        ),
        type_=sa.VARCHAR(length=30),
        existing_nullable=False,
    )
