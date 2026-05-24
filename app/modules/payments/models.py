"""Saved card and webhook/fee models.

Braintree-vaulted credit cards (never raw card data -- PCI SAQ-A) live in
``credit_cards``. Each row belongs to either an organization (B2B) or a user
(B2C) -- exactly one must be set.

``OrgPaymentMethod`` in organizations is a separate table (per-org payment
model configuration).
"""

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AppendOnlyModel, BaseModel, BaseModelNoVersion


class CreditCard(BaseModel):
    """Saved Braintree card -- vault token + masked card details.

    Ownership: exactly one of organization_id or user_id must be set.
    - B2B: organization_id is set (card belongs to the org)
    - B2C: user_id is set (card belongs to the individual)

    Up to MAX_PAYMENT_METHODS_PER_OWNER cards per owner share one Braintree Customer (vault); each card is a
    separate payment method and may have a different cardholder_name than sibling cards.

    NO raw card data is ever stored. PCI SAQ-A compliance.
    """

    __tablename__ = "credit_cards"

    organization_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", name="credit_cards_organization_id_fkey", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="credit_cards_user_id_fkey", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="credit_cards_created_by_id_fkey", ondelete="SET NULL"),
        nullable=True,
    )

    # -- Braintree vault --
    braintree_token: Mapped[str] = mapped_column(String(255), nullable=False)
    braintree_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # -- Masked card info (safe to store) --
    card_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    country_of_issuance: Mapped[str | None] = mapped_column(String(2), nullable=True)
    last_four: Mapped[str | None] = mapped_column(String(4), nullable=True)
    expiry_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expiry_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cardholder_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")

    # -- Relationships --
    organization = relationship("Organization", lazy="raise", foreign_keys=[organization_id])
    user = relationship("User", lazy="raise", foreign_keys=[user_id])

    __table_args__ = (
        CheckConstraint(
            "(organization_id IS NOT NULL AND user_id IS NULL) OR " "(organization_id IS NULL AND user_id IS NOT NULL)",
            name="ck_credit_cards_owner",
        ),
    )

    def __repr__(self) -> str:
        owner = f"org={self.organization_id}" if self.organization_id else f"user={self.user_id}"
        return f"<CreditCard ****{self.last_four} {owner}>"


class BraintreeFeeProfile(BaseModelNoVersion):
    __tablename__ = "braintree_fee_profiles"
    __table_args__ = (
        UniqueConstraint("market", "currency", "card_brand", name="uq_braintree_fee_market_currency_brand"),
    )

    market: Mapped[str] = mapped_column(String(20), nullable=False, default="UK")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    card_brand: Mapped[str] = mapped_column(String(30), nullable=False, default="STANDARD")
    rate_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=Decimal("0"))
    fixed_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"))
    cross_border_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=Decimal("0"))
    scheme_currency_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=Decimal("0"))
    exotic_currency_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=Decimal("0"))
    chargeback_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BraintreeWebhookEvent(AppendOnlyModel):
    __tablename__ = "braintree_webhook_events"

    webhook_kind: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    braintree_transaction_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    dispute_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
