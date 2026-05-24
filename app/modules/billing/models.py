"""Billing payment ledger models."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import Sequence

from app.common.models import AppendOnlyModel, Base, BaseModel, BaseModelNoVersion

payment_number_seq = Sequence("payment_number_seq", metadata=Base.metadata)


class BillingPayment(BaseModel):
    """Canonical payment row (source of truth)."""

    __tablename__ = "billing_payments"

    payment_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'PAY-' || lpad(nextval('{payment_number_seq.name}')::text, 6, '0')"),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    recorded_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="billing_payments_recorded_by_id_fkey", ondelete="SET NULL"),
        nullable=True,
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    transaction_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"), server_default="0")
    dispute_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"), server_default="0")
    dispute_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"), server_default="0")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")

    status: Mapped[str] = mapped_column(String(25), nullable=False, default="NOT_DEPOSITED", server_default="NOT_DEPOSITED", index=True)
    allocation_status: Mapped[str] = mapped_column(String(25), nullable=False, default="UNALLOCATED", server_default="UNALLOCATED", index=True)

    # Read-optimized denormalized projections maintained by billing service.
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"), server_default="0")
    unallocated_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"), server_default="0")

    payment_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="MANUAL", server_default="MANUAL")
    provider_txn_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    braintree_status: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    dispute_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    braintree_status_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    remittance_advice_r2_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    remittance_advice_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    remittance_advice_original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remittance_advice_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remittance_advice_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    qb_sync_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NOT_SYNCED", server_default="NOT_SYNCED", index=True)
    qb_last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    qb_payload_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    organization = relationship("Organization", lazy="raise", foreign_keys=[organization_id])
    customer = relationship("User", lazy="raise", foreign_keys=[customer_id])
    recorded_by = relationship("User", lazy="raise", foreign_keys=[recorded_by_id])

    allocations = relationship(
        "BillingPaymentAllocation",
        back_populates="payment",
        lazy="raise",
        cascade="all, delete-orphan",
    )
    events = relationship(
        "BillingPaymentEvent",
        back_populates="payment",
        lazy="raise",
        order_by="BillingPaymentEvent.created_at",
    )
    refunds = relationship(
        "Refund",
        back_populates="payment",
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_billing_payments_amount_positive"),
        CheckConstraint("transaction_fee >= 0", name="ck_billing_payments_transaction_fee_non_negative"),
        CheckConstraint("dispute_amount >= 0", name="ck_billing_payments_dispute_amount_non_negative"),
        CheckConstraint("dispute_fee >= 0", name="ck_billing_payments_dispute_fee_non_negative"),
        CheckConstraint("allocated_amount >= 0", name="ck_billing_payments_allocated_non_negative"),
        CheckConstraint("unallocated_amount >= 0", name="ck_billing_payments_unallocated_non_negative"),
        UniqueConstraint("organization_id", "provider", "provider_txn_id", name="uq_billing_payments_org_provider_txn"),
    )


class BillingPaymentAllocation(BaseModelNoVersion):
    """Append-only allocation revisions for payment->invoice mapping."""

    __tablename__ = "billing_payment_allocations"

    payment_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("billing_payments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    allocated_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    payment = relationship("BillingPayment", back_populates="allocations", lazy="raise")
    invoice = relationship("Invoice", lazy="raise", foreign_keys=[invoice_id])

    __table_args__ = (
        UniqueConstraint("payment_id", "invoice_id", "revision_no", name="uq_billing_allocations_payment_invoice_revision"),
        CheckConstraint("allocated_amount >= 0", name="ck_billing_allocations_amount_non_negative"),
    )


class BillingPaymentEvent(AppendOnlyModel):
    """Append-only event log for billing payment lifecycle and sync outcomes."""

    __tablename__ = "billing_payment_events"

    payment_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("billing_payments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    payment = relationship("BillingPayment", back_populates="events", lazy="raise")


class Refund(BaseModel):
    """Refund row for billing payment reversal tracking."""

    __tablename__ = "refunds"

    refund_number: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    billing_payment_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("billing_payments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    linked_booking_ref: Mapped[str | None] = mapped_column(String(50), nullable=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="MANUAL", server_default="MANUAL")
    refund_method: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    refund_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="INITIATED", server_default="INITIATED", index=True)
    reason_category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    reason_description: Mapped[str] = mapped_column(Text, nullable=False)
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    processed_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"), server_default="0")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP", server_default="GBP")
    braintree_transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    braintree_status: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    braintree_status_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failure_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    initiated_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    processed_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    initiated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    payment = relationship("BillingPayment", back_populates="refunds", lazy="raise")
    invoice = relationship("Invoice", lazy="raise", foreign_keys=[invoice_id])
    events = relationship(
        "RefundEvent",
        back_populates="refund",
        lazy="raise",
        order_by="RefundEvent.created_at",
    )

    __table_args__ = (
        CheckConstraint("requested_amount > 0", name="ck_refunds_requested_amount_positive"),
        CheckConstraint("processed_amount >= 0", name="ck_refunds_processed_amount_non_negative"),
        CheckConstraint("processed_amount <= requested_amount", name="ck_refunds_processed_lte_requested"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_refunds_org_idempotency_key"),
    )


class RefundEvent(AppendOnlyModel):
    """Append-only event log for refund lifecycle."""

    __tablename__ = "refund_events"

    refund_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("refunds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    refund = relationship("Refund", back_populates="events", lazy="raise")
