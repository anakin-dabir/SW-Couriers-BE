"""Account statement ORM models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import Sequence

from app.common.models import Base, BaseModel, BaseModelNoVersion

statement_number_seq = Sequence("account_statement_number_seq", metadata=Base.metadata)


class AccountStatement(BaseModel):
    """Generated account statement for a B2B organization."""

    __tablename__ = "account_statements"

    statement_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'ST-' || lpad(nextval('{statement_number_seq.name}')::text, 6, '0')"),
    )
    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    closing_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_invoice_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    total_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    total_unpaid: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    total_overdue: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    aging_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

    include_line_item_detail: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    include_credit_notes: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    include_payment_history: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")

    pdf_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    pdf_r2_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pdf_template_version: Mapped[str] = mapped_column(String(30), nullable=False, default="v1")
    content_signature: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    job_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_user_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    snapshot_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    organization = relationship("Organization", lazy="raise", foreign_keys=[organization_id])
    created_by = relationship("User", lazy="raise", foreign_keys=[created_by_user_id])
    delivery_events = relationship(
        "AccountStatementDeliveryEvent",
        back_populates="statement",
        lazy="raise",
        order_by="AccountStatementDeliveryEvent.created_at",
    )


class AccountStatementSchedule(BaseModel):
    """Recurring statement generation schedule for an organization."""

    __tablename__ = "account_statement_schedules"

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    frequency: Mapped[str] = mapped_column(String(30), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(Date, nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/London")
    include_line_item_detail: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    include_credit_notes: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    include_payment_history: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    custom_cron: Mapped[str | None] = mapped_column(String(120), nullable=True)

    organization = relationship("Organization", lazy="raise", foreign_keys=[organization_id])


class AccountStatementDeliveryEvent(BaseModelNoVersion):
    """Audit of statement email deliveries."""

    __tablename__ = "account_statement_delivery_events"

    statement_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("account_statements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    statement = relationship("AccountStatement", back_populates="delivery_events", lazy="raise")
