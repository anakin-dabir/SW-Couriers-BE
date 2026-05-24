"""refunds management foundation

Revision ID: 0111_refunds_management
Revises: 0110_bt_webhooks_and_fee
Create Date: 2026-05-06
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0111_refunds_management"
down_revision: str | None = "0110_bt_webhooks_and_fee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("billing_payments", sa.Column("transaction_fee", sa.Numeric(10, 2), nullable=False, server_default="0"))
    op.add_column("billing_payments", sa.Column("braintree_status", sa.String(length=50), nullable=True))
    op.add_column("billing_payments", sa.Column("braintree_status_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_billing_payments_braintree_status", "billing_payments", ["braintree_status"])
    op.create_check_constraint(
        "ck_billing_payments_transaction_fee_non_negative",
        "billing_payments",
        "transaction_fee >= 0",
    )

    op.create_table(
        "refunds",
        sa.Column("refund_number", sa.String(length=30), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("billing_payment_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("linked_booking_ref", sa.String(length=50), nullable=True),
        sa.Column("provider", sa.String(length=30), server_default="MANUAL", nullable=False),
        sa.Column("refund_method", sa.String(length=30), nullable=False),
        sa.Column("refund_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="INITIATED", nullable=False),
        sa.Column("reason_category", sa.String(length=40), nullable=False),
        sa.Column("reason_description", sa.Text(), nullable=False),
        sa.Column("requested_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("processed_amount", sa.Numeric(10, 2), server_default="0", nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="GBP", nullable=False),
        sa.Column("braintree_transaction_id", sa.String(length=255), nullable=True),
        sa.Column("braintree_status", sa.String(length=50), nullable=True),
        sa.Column("braintree_status_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_code", sa.String(length=50), nullable=True),
        sa.Column("failure_message", sa.String(length=500), nullable=True),
        sa.Column("initiated_by_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("processed_by_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("initiated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["billing_payment_id"], ["billing_payments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["initiated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["processed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_refunds_org_idempotency_key"),
        sa.UniqueConstraint("refund_number"),
        sa.CheckConstraint("requested_amount > 0", name="ck_refunds_requested_amount_positive"),
        sa.CheckConstraint("processed_amount >= 0", name="ck_refunds_processed_amount_non_negative"),
        sa.CheckConstraint("processed_amount <= requested_amount", name="ck_refunds_processed_lte_requested"),
    )
    op.create_index("ix_refunds_refund_number", "refunds", ["refund_number"])
    op.create_index("ix_refunds_organization_id", "refunds", ["organization_id"])
    op.create_index("ix_refunds_billing_payment_id", "refunds", ["billing_payment_id"])
    op.create_index("ix_refunds_invoice_id", "refunds", ["invoice_id"])
    op.create_index("ix_refunds_status", "refunds", ["status"])
    op.create_index("ix_refunds_refund_method", "refunds", ["refund_method"])
    op.create_index("ix_refunds_refund_type", "refunds", ["refund_type"])
    op.create_index("ix_refunds_reason_category", "refunds", ["reason_category"])
    op.create_index("ix_refunds_braintree_transaction_id", "refunds", ["braintree_transaction_id"])
    op.create_index("ix_refunds_braintree_status", "refunds", ["braintree_status"])
    op.create_index("ix_refunds_org_status_created", "refunds", ["organization_id", "status", "created_at"])
    op.create_index("ix_refunds_org_created", "refunds", ["organization_id", "created_at"])

    op.create_table(
        "refund_events",
        sa.Column("refund_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["refund_id"], ["refunds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refund_events_refund_id", "refund_events", ["refund_id"])
    op.create_index("ix_refund_events_event_type", "refund_events", ["event_type"])
    op.create_index("ix_refund_events_refund_created", "refund_events", ["refund_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_refund_events_refund_created", table_name="refund_events")
    op.drop_index("ix_refund_events_event_type", table_name="refund_events")
    op.drop_index("ix_refund_events_refund_id", table_name="refund_events")
    op.drop_table("refund_events")

    op.drop_index("ix_refunds_org_created", table_name="refunds")
    op.drop_index("ix_refunds_org_status_created", table_name="refunds")
    op.drop_index("ix_refunds_braintree_status", table_name="refunds")
    op.drop_index("ix_refunds_braintree_transaction_id", table_name="refunds")
    op.drop_index("ix_refunds_reason_category", table_name="refunds")
    op.drop_index("ix_refunds_refund_type", table_name="refunds")
    op.drop_index("ix_refunds_refund_method", table_name="refunds")
    op.drop_index("ix_refunds_status", table_name="refunds")
    op.drop_index("ix_refunds_invoice_id", table_name="refunds")
    op.drop_index("ix_refunds_billing_payment_id", table_name="refunds")
    op.drop_index("ix_refunds_organization_id", table_name="refunds")
    op.drop_index("ix_refunds_refund_number", table_name="refunds")
    op.drop_table("refunds")

    op.drop_constraint("ck_billing_payments_transaction_fee_non_negative", "billing_payments", type_="check")
    op.drop_index("ix_billing_payments_braintree_status", table_name="billing_payments")
    op.drop_column("billing_payments", "braintree_status_updated_at")
    op.drop_column("billing_payments", "braintree_status")
    op.drop_column("billing_payments", "transaction_fee")
