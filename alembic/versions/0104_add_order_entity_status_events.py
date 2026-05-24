"""add order delivery_stop and package status event tables

Revision ID: 0104_order_entity_status_events
Revises: 0103_orders_pickup_date
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0104_order_entity_status_events"
down_revision: str | None = "0103_orders_pickup_date"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "order_events",
        sa.Column("id", UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("order_id", UUID(as_uuid=False), nullable=False),
        sa.Column("from_status", sa.String(length=64), nullable=True),
        sa.Column("to_status", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", UUID(as_uuid=False), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name="fk_order_events_order_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_order_events_actor_user_id", ondelete="SET NULL"),
    )
    op.create_index("ix_order_events_order_id", "order_events", ["order_id"])
    op.create_index("ix_order_events_order_id_created_at", "order_events", ["order_id", "created_at"])

    op.create_table(
        "delivery_stop_events",
        sa.Column("id", UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("delivery_stop_id", UUID(as_uuid=False), nullable=False),
        sa.Column("from_status", sa.String(length=64), nullable=True),
        sa.Column("to_status", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", UUID(as_uuid=False), nullable=True),
        sa.ForeignKeyConstraint(
            ["delivery_stop_id"],
            ["delivery_stops.id"],
            name="fk_delivery_stop_events_stop_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_delivery_stop_events_actor_user_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_delivery_stop_events_delivery_stop_id", "delivery_stop_events", ["delivery_stop_id"])
    op.create_index(
        "ix_delivery_stop_events_stop_id_created_at",
        "delivery_stop_events",
        ["delivery_stop_id", "created_at"],
    )

    op.create_table(
        "package_events",
        sa.Column("id", UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("package_id", UUID(as_uuid=False), nullable=False),
        sa.Column("from_status", sa.String(length=64), nullable=True),
        sa.Column("to_status", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", UUID(as_uuid=False), nullable=True),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["packages.id"],
            name="fk_package_events_package_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_package_events_actor_user_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_package_events_package_id", "package_events", ["package_id"])
    op.create_index("ix_package_events_package_id_created_at", "package_events", ["package_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_package_events_package_id_created_at", table_name="package_events")
    op.drop_index("ix_package_events_package_id", table_name="package_events")
    op.drop_table("package_events")

    op.drop_index("ix_delivery_stop_events_stop_id_created_at", table_name="delivery_stop_events")
    op.drop_index("ix_delivery_stop_events_delivery_stop_id", table_name="delivery_stop_events")
    op.drop_table("delivery_stop_events")

    op.drop_index("ix_order_events_order_id_created_at", table_name="order_events")
    op.drop_index("ix_order_events_order_id", table_name="order_events")
    op.drop_table("order_events")
