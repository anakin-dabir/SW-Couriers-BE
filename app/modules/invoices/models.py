"""Invoice and invoice line item models. B2B billing after service completion.

Entities:
- Invoice: main document (lifecycle DRAFT | SENT; payment outcome is derived).
- InvoiceLineItem: line items on an invoice (no independent versioning).
- InvoiceEvent: append-only audit of lifecycle events (created, finalized, voided, etc.).
- CreditNote: credit memo; applied to invoices via InvoiceCreditApplication.
- InvoiceCreditApplication: links credit note to invoice, reduces outstanding balance.
- InvoicePdfArtifact: generated PDF per (invoice, template_version, signature); stored in R2.
All monetary amounts use Numeric(10,2) / Decimal for precision.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import Sequence

from app.common.models import AppendOnlyModel, Base, BaseModel, BaseModelNoVersion

invoice_number_seq = Sequence("invoice_number_seq", metadata=Base.metadata)


class Invoice(BaseModel):
    """B2B invoice — generated after service completion. One per order. invoice_number format INV-NNNNNN."""

    __tablename__ = "invoices"

    # Human-facing identifier, format INV-NNNNNN (e.g. INV-000001). Unique.
    invoice_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'INV-' || lpad(nextval('{invoice_number_seq.name}')::text, 6, '0')"),
    )

    # ── Client ───────────────────────────────
    organization_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    customer_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    order_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Amounts (Numeric for financial precision) ──
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("20.0"))
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    payment_status: Mapped[str] = mapped_column(String(20), nullable=False, default="UNPAID", index=True)
    braintree_transaction_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")

    # ── Dates ────────────────────────────────
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)

    # ── Status: lifecycle (document state) ──
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="DRAFT", index=True)  # invoice lifecycle: DRAFT | SENT only

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    billing_contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    qb_sync_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NOT_SYNCED", server_default="NOT_SYNCED", index=True)
    qb_last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    qb_payload_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # ── Relationships ────────────────────────
    organization = relationship("Organization", lazy="raise", foreign_keys=[organization_id])
    order = relationship("Order", lazy="raise", foreign_keys=[order_id])
    line_items = relationship("InvoiceLineItem", back_populates="invoice", lazy="raise", cascade="all, delete-orphan")
    events = relationship("InvoiceEvent", back_populates="invoice", lazy="raise", order_by="InvoiceEvent.created_at")
    credit_applications = relationship(
        "InvoiceCreditApplication",
        back_populates="invoice",
        lazy="raise",
        foreign_keys="InvoiceCreditApplication.invoice_id",
    )
    pdf_artifacts = relationship(
        "InvoicePdfArtifact",
        back_populates="invoice",
        lazy="raise",
        order_by="InvoicePdfArtifact.created_at",
    )

    def __repr__(self) -> str:
        return f"<Invoice {self.invoice_number} status={self.status}>"


class InvoiceEvent(AppendOnlyModel):
    """Append-only invoice activity (created, finalized, voided, written off, credit applied)."""

    __tablename__ = "invoice_events"

    invoice_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_role: Mapped[str | None] = mapped_column(String(30), nullable=True)

    invoice = relationship("Invoice", back_populates="events", lazy="raise")

    def __repr__(self) -> str:
        return f"<InvoiceEvent {self.event_type} invoice={self.invoice_id}>"


class CreditNote(BaseModel):
    """Credit memo — credit given to customer. Applied to invoices via InvoiceCreditApplication."""

    __tablename__ = "credit_notes"

    credit_note_number: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    organization_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    customer_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_invoice_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_credit_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ISSUED", index=True)
    reason_category: Mapped[str] = mapped_column(String(40), nullable=False, default="OTHER", server_default="OTHER", index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_to_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    qb_sync_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NOT_SYNCED", server_default="NOT_SYNCED", index=True)
    qb_last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    qb_payload_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    reversal_invoice_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        unique=True,
    )

    organization = relationship("Organization", lazy="raise", foreign_keys=[organization_id])
    source_invoice = relationship("Invoice", lazy="raise", foreign_keys=[source_invoice_id])
    reversal_invoice = relationship("Invoice", lazy="raise", foreign_keys=[reversal_invoice_id])
    applications = relationship(
        "InvoiceCreditApplication",
        back_populates="credit_note",
        lazy="raise",
    )
    pdf_artifacts = relationship(
        "CreditNotePdfArtifact",
        back_populates="credit_note",
        lazy="raise",
        order_by="CreditNotePdfArtifact.created_at",
    )

    def __repr__(self) -> str:
        return f"<CreditNote {self.credit_note_number} status={self.status}>"


class InvoiceCreditApplication(BaseModelNoVersion):
    """Application of a credit note to an invoice. Reduces outstanding balance."""

    __tablename__ = "invoice_credit_applications"

    invoice_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credit_note_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("credit_notes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    applied_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    applied_at: Mapped[date] = mapped_column(Date, nullable=False)
    applied_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    invoice = relationship("Invoice", back_populates="credit_applications", lazy="raise", foreign_keys=[invoice_id])
    credit_note = relationship("CreditNote", back_populates="applications", lazy="raise")

    def __repr__(self) -> str:
        return f"<InvoiceCreditApplication invoice={self.invoice_id} credit_note={self.credit_note_id} {self.applied_amount}>"


class InvoicePdfArtifact(BaseModelNoVersion):
    """Immutable PDF artifact per (invoice, template_version, signature_hash). History kept."""

    __tablename__ = "invoice_pdf_artifacts"

    invoice_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    template_version: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    signature_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    pdf_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="GENERATING", index=True)
    r2_file_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    invoice = relationship("Invoice", back_populates="pdf_artifacts", lazy="raise")

    def __repr__(self) -> str:
        return f"<InvoicePdfArtifact invoice={self.invoice_id} v{self.pdf_version} status={self.status}>"


class CreditNotePdfArtifact(BaseModelNoVersion):
    """Immutable PDF artifact per credit note signature hash."""

    __tablename__ = "credit_note_pdf_artifacts"

    credit_note_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("credit_notes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    template_version: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    signature_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    pdf_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="GENERATING", index=True)
    r2_file_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    credit_note = relationship("CreditNote", back_populates="pdf_artifacts", lazy="raise")

    def __repr__(self) -> str:
        return f"<CreditNotePdfArtifact credit_note={self.credit_note_id} v{self.pdf_version} status={self.status}>"


class InvoiceLineItem(BaseModelNoVersion):
    """Individual line item on an invoice. No independent versioning — always
    updated as part of the parent invoice transaction."""

    __tablename__ = "invoice_line_items"

    invoice_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    package_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("packages.id", ondelete="SET NULL"),
        nullable=True,
    )

    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    total_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # ── Categorization ───────────────────────
    line_type: Mapped[str] = mapped_column(String(30), nullable=False, default="service")  # service, surcharge, discount

    invoice = relationship("Invoice", back_populates="line_items", lazy="raise")

    def __repr__(self) -> str:
        return f"<InvoiceLineItem {self.description} £{self.total_price}>"
