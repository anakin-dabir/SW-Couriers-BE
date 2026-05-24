"""Create billing payment ledger foundation tables.

Revision ID: 0084_billing_payments_foundation
Revises: 0083_orders_module
Create Date: 2026-04-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0084_billing_payments_found"
down_revision: str | None = "0083_orders_module"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS payment_number_seq START 1 INCREMENT 1")

    op.create_table(
        "billing_payments",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "payment_number",
            sa.String(30),
            nullable=False,
            server_default=sa.text("'PAY-' || lpad(nextval('payment_number_seq')::text, 6, '0')"),
        ),
        sa.Column("organization_id", UUID(as_uuid=False), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("customer_id", UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "recorded_by_id",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="billing_payments_recorded_by_id_fkey"),
            nullable=True,
        ),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="GBP"),
        sa.Column("status", sa.String(25), nullable=False, server_default="NOT_DEPOSITED"),
        sa.Column("allocation_status", sa.String(25), nullable=False, server_default="UNALLOCATED"),
        sa.Column("allocated_amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("unallocated_amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False, server_default="MANUAL"),
        sa.Column("provider_txn_id", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=True),
        sa.Column("qb_sync_status", sa.String(20), nullable=False, server_default="NOT_SYNCED"),
        sa.Column("qb_last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qb_payload_fingerprint", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_billing_payments_amount_positive"),
        sa.CheckConstraint("allocated_amount >= 0", name="ck_billing_payments_allocated_non_negative"),
        sa.CheckConstraint("unallocated_amount >= 0", name="ck_billing_payments_unallocated_non_negative"),
        sa.UniqueConstraint("payment_number", name="uq_billing_payments_payment_number"),
        sa.UniqueConstraint("organization_id", "provider", "provider_txn_id", name="uq_billing_payments_org_provider_txn"),
    )
    op.create_index("ix_billing_payments_payment_number", "billing_payments", ["payment_number"])
    op.create_index("ix_billing_payments_organization_id", "billing_payments", ["organization_id"])
    op.create_index("ix_billing_payments_customer_id", "billing_payments", ["customer_id"])
    op.create_index("ix_billing_payments_status", "billing_payments", ["status"])
    op.create_index("ix_billing_payments_allocation_status", "billing_payments", ["allocation_status"])
    op.create_index("ix_billing_payments_payment_date", "billing_payments", ["payment_date"])
    op.create_index("ix_billing_payments_qb_sync_status", "billing_payments", ["qb_sync_status"])
    op.create_index("ix_billing_payments_qb_payload_fingerprint", "billing_payments", ["qb_payload_fingerprint"])

    op.create_table(
        "billing_payment_allocations",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("payment_id", UUID(as_uuid=False), sa.ForeignKey("billing_payments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_id", UUID(as_uuid=False), sa.ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("allocated_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("allocated_by_id", UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("allocated_amount >= 0", name="ck_billing_allocations_amount_non_negative"),
        sa.UniqueConstraint("payment_id", "invoice_id", "revision_no", name="uq_billing_allocations_payment_invoice_revision"),
    )
    op.create_index("ix_billing_payment_allocations_payment_id", "billing_payment_allocations", ["payment_id"])
    op.create_index("ix_billing_payment_allocations_invoice_id", "billing_payment_allocations", ["invoice_id"])

    op.create_table(
        "billing_payment_events",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("payment_id", UUID(as_uuid=False), sa.ForeignKey("billing_payments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("payload_json", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_billing_payment_events_payment_id", "billing_payment_events", ["payment_id"])
    op.create_index("ix_billing_payment_events_event_type", "billing_payment_events", ["event_type"])

    # Invoice payment columns are retained in this revised 0082.


def downgrade() -> None:

    op.drop_index("ix_billing_payment_events_event_type", table_name="billing_payment_events")
    op.drop_index("ix_billing_payment_events_payment_id", table_name="billing_payment_events")
    op.drop_table("billing_payment_events")

    op.drop_index("ix_billing_payment_allocations_invoice_id", table_name="billing_payment_allocations")
    op.drop_index("ix_billing_payment_allocations_payment_id", table_name="billing_payment_allocations")
    op.drop_table("billing_payment_allocations")

    op.drop_index("ix_billing_payments_qb_payload_fingerprint", table_name="billing_payments")
    op.drop_index("ix_billing_payments_qb_sync_status", table_name="billing_payments")
    op.drop_index("ix_billing_payments_payment_date", table_name="billing_payments")
    op.drop_index("ix_billing_payments_allocation_status", table_name="billing_payments")
    op.drop_index("ix_billing_payments_status", table_name="billing_payments")
    op.drop_index("ix_billing_payments_customer_id", table_name="billing_payments")
    op.drop_index("ix_billing_payments_organization_id", table_name="billing_payments")
    op.drop_index("ix_billing_payments_payment_number", table_name="billing_payments")
    op.drop_table("billing_payments")

    op.execute("DROP SEQUENCE IF EXISTS payment_number_seq")
