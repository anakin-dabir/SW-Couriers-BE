"""Stop failed + return attempt tables

Revision ID: 0151_stop_attempt_tables
Revises: 0150_order_drafts_total_amount
Create Date: 2026-05-22 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0151_stop_attempt_tables"
down_revision: Union[str, None] = "0150_order_drafts_total_amount"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_attempts_table(name: str, unique_name: str) -> None:
    op.create_table(
        name,
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("delivery_stop_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("route_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(
            ["delivery_stop_id"], ["delivery_stops.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["route_id"], ["routes.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("delivery_stop_id", "attempt_number", name=unique_name),
    )
    op.create_index(
        f"ix_{name}_delivery_stop_id",
        name,
        ["delivery_stop_id"],
        unique=False,
    )


def _drop_attempts_table(name: str) -> None:
    op.drop_index(f"ix_{name}_delivery_stop_id", table_name=name)
    op.drop_table(name)


def upgrade() -> None:
    _create_attempts_table(
        "delivery_stop_failed_attempts", "uq_dsfa_stop_attempt_number"
    )
    _create_attempts_table(
        "delivery_stop_return_attempts", "uq_dsra_stop_attempt_number"
    )


def downgrade() -> None:
    _drop_attempts_table("delivery_stop_return_attempts")
    _drop_attempts_table("delivery_stop_failed_attempts")
