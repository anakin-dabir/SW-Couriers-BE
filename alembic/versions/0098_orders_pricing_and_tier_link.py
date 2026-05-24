"""orders pricing + tier link + flexible tier / payment columns.

Three sets of changes, all in one migration because they are part of the same
orders-pricing rollout:

Revision ID: 0098_orders_pricing
Revises: 0097_org_global_suppress
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op


revision: str = "0098_orders_pricing"
down_revision: str | None = "0097_org_global_suppress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_delivery_stop_service_tier_enum = sa.Enum(
    "FASTEST",
    "STANDARD",
    "ECONOMY",
    name="delivery_stop_service_tier_enum",
    native_enum=False,
)

_order_payment_method_enum_old = sa.Enum(
    "CARD_PAYMENT",
    "BANK_TRANSFER",
    "CREDIT_ACCOUNT",
    "CASH",
    name="order_payment_method_enum",
    native_enum=False,
)


def upgrade() -> None:
    op.add_column(
        "delivery_stops",
        sa.Column("price_breakdown", JSONB, nullable=True),
    )
    op.add_column(
        "delivery_stops",
        sa.Column("service_tier_id", UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_delivery_stops_service_tier_id",
        "delivery_stops",
        "service_tier",
        ["service_tier_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_delivery_stops_service_tier_id",
        "delivery_stops",
        ["service_tier_id"],
    )

    op.alter_column(
        "delivery_stops",
        "service_tier",
        existing_type=_delivery_stop_service_tier_enum,
        type_=sa.String(length=100),
        existing_nullable=True,
        postgresql_using="service_tier::text",
    )

    op.add_column(
        "packages",
        sa.Column("price_breakdown", JSONB, nullable=True),
    )

    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_payment_method_id_fkey")
    op.execute("UPDATE orders SET payment_method_id = NULL")

    op.alter_column(
        "orders",
        "payment_method",
        existing_type=_order_payment_method_enum_old,
        type_=sa.String(length=30),
        existing_nullable=True,
        postgresql_using="payment_method::text",
    )
    op.execute(
        "UPDATE orders SET payment_method = 'CARD' WHERE payment_method = 'CARD_PAYMENT'"
    )

    op.create_foreign_key(
        "fk_orders_org_payment_method_id",
        "orders",
        "org_payment_methods",
        ["payment_method_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint("uq_orders_order_id", "orders", type_="unique")
    op.drop_index("ix_orders_order_id", table_name="orders")
    op.create_index("ix_orders_order_id", "orders", ["order_id"], unique=True)

    op.drop_constraint("uq_orders_master_label_id", "orders", type_="unique")
    op.drop_index("ix_orders_master_label_id", table_name="orders")
    op.create_index("ix_orders_master_label_id", "orders", ["master_label_id"], unique=True)

    op.drop_constraint("uq_order_drafts_draft_id", "order_drafts", type_="unique")
    op.drop_index("ix_order_drafts_draft_id", table_name="order_drafts")
    op.create_index("ix_order_drafts_draft_id", "order_drafts", ["draft_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_order_drafts_draft_id", table_name="order_drafts")
    op.create_index("ix_order_drafts_draft_id", "order_drafts", ["draft_id"])
    op.create_unique_constraint("uq_order_drafts_draft_id", "order_drafts", ["draft_id"])

    op.drop_index("ix_orders_master_label_id", table_name="orders")
    op.create_index("ix_orders_master_label_id", "orders", ["master_label_id"])
    op.create_unique_constraint("uq_orders_master_label_id", "orders", ["master_label_id"])

    op.drop_index("ix_orders_order_id", table_name="orders")
    op.create_index("ix_orders_order_id", "orders", ["order_id"])
    op.create_unique_constraint("uq_orders_order_id", "orders", ["order_id"])

    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS fk_orders_org_payment_method_id")
    op.execute("UPDATE orders SET payment_method_id = NULL")

    op.execute(
        "UPDATE orders SET payment_method = 'CARD_PAYMENT' WHERE payment_method = 'CARD'"
    )
    op.alter_column(
        "orders",
        "payment_method",
        existing_type=sa.String(length=30),
        type_=_order_payment_method_enum_old,
        existing_nullable=True,
        postgresql_using="payment_method::text",
    )

    op.create_foreign_key(
        "orders_payment_method_id_fkey",
        "orders",
        "payment_methods",
        ["payment_method_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_column("packages", "price_breakdown")

    op.alter_column(
        "delivery_stops",
        "service_tier",
        existing_type=sa.String(length=100),
        type_=_delivery_stop_service_tier_enum,
        existing_nullable=True,
        postgresql_using="service_tier::text",
    )

    op.drop_index("ix_delivery_stops_service_tier_id", table_name="delivery_stops")
    op.drop_constraint("fk_delivery_stops_service_tier_id", "delivery_stops", type_="foreignkey")
    op.drop_column("delivery_stops", "service_tier_id")
    op.drop_column("delivery_stops", "price_breakdown")
