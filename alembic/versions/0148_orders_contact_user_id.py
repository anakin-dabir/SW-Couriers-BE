"""Add ``orders.contact_user_id`` to track the B2B booking contact separately.

Until the B2C order path is implemented, the contact's ``user_id`` from the
create-order request body was being stored in ``orders.customer_id``. Going
forward ``customer_id`` is meant to be the B2C customer only, so we add a
dedicated ``contact_user_id`` column for the B2B contact who placed the
booking. The application code populates it for B2B orders and leaves it
``NULL`` for B2C orders. No backfill — the column is nullable and historical
rows can be filled in by a follow-up data migration once the B2C split is
complete.

``OrderDraft`` is unchanged — drafts store the full payload (including
``contact_user_id``) in their JSONB ``payload`` column.

Revision ID: 0148_orders_contact_user_id
Revises: 0147_b2b_user_status_pending
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0148_orders_contact_user_id"
down_revision: str | None = "0147_b2b_user_status_pending"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("contact_user_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_orders_contact_user_id",
        "orders",
        "users",
        ["contact_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_orders_contact_user_id", "orders", ["contact_user_id"])

    op.alter_column("orders", "customer_id", existing_type=postgresql.UUID(as_uuid=False), nullable=True)


def downgrade() -> None:
    op.alter_column("orders", "customer_id", existing_type=postgresql.UUID(as_uuid=False), nullable=False)

    op.drop_index("ix_orders_contact_user_id", table_name="orders")
    op.drop_constraint("fk_orders_contact_user_id", "orders", type_="foreignkey")
    op.drop_column("orders", "contact_user_id")
