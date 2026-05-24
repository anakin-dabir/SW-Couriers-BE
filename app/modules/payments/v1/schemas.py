from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.common.schemas import BaseSchema, CurrencyAmount
from app.modules.payments.enums import PaymentMethodStatus


class CreatePaymentMethodRequest(BaseSchema):
    """Client sends a Braintree payment method nonce obtained from Hosted Fields / Drop-in UI."""

    nonce: str = Field(min_length=1, max_length=500, description="Braintree payment method nonce from client-side tokenization")
    cardholder_name: str | None = Field(default=None, min_length=1, max_length=255)
    set_as_default: bool = Field(default=False, description="Set this card as the default payment method")


class DevCreateRawCardRequest(BaseSchema):
    """DEV ONLY: raw card entry to create a vaulted card from backend."""

    card_number: str = Field(min_length=12, max_length=19)
    expiry_month: int = Field(ge=1, le=12)
    expiry_year: int = Field(ge=2024, le=2099)
    cvv: str = Field(min_length=3, max_length=4)
    cardholder_name: str | None = Field(default=None, min_length=1, max_length=255)
    set_as_default: bool = Field(default=True)


class DevChargeSavedCardRequest(BaseSchema):
    """DEV ONLY: charge a saved card to test booking payment flow."""

    credit_card_id: str
    nonce: str = Field(
        min_length=1,
        max_length=500,
        description="Payment method nonce from client threeDSecure.verifyCard (after authentication)",
    )
    amount: CurrencyAmount
    order_id: str | None = Field(default=None, max_length=255)


class DevChargeSavedCardResponse(BaseSchema):
    success: bool
    braintree_transaction_id: str | None = None
    payment_status: str
    processor_message: str | None = None
    amount: Decimal
    transaction_fee: Decimal | None = None
    expected_fee_amount: Decimal | None = None
    fee_card_brand: str | None = None
    country_of_issuance: str | None = None


class DevReverseTransactionRequest(BaseSchema):
    transaction_id: str = Field(min_length=1, max_length=100)
    amount: CurrencyAmount | None = None


class DevReverseTransactionResponse(BaseSchema):
    success: bool
    action: str
    original_transaction_id: str
    transaction_id: str | None = None
    processor_message: str | None = None


class ClientTokenResponse(BaseSchema):
    client_token: str = Field(description="Braintree client token for initializing Hosted Fields or Drop-in UI on the frontend")


class PreparePaymentNonceRequest(BaseSchema):
    """Select a saved card to obtain a one-time nonce for client-side 3DS (verifyCard) before charging."""

    card_id: str = Field(min_length=1, max_length=50, description="Saved payment method id from list/get card endpoints")


class PreparePaymentNonceResponse(BaseSchema):
    """Nonce and BIN for Braintree.js ``threeDSecure.verifyCard`` with the order amount."""

    nonce: str = Field(description="One-time nonce referencing the vaulted card; use with verifyCard then sale")
    bin: str | None = Field(default=None, description="First digits for verifyCard when Braintree returns them")


class PaymentMethodResponse(BaseSchema):
    id: str
    card_type: str | None = None
    country_of_issuance: str | None = None
    last_four: str | None = None
    expiry_month: int | None = None
    expiry_year: int | None = None
    cardholder_name: str | None = None
    is_default: bool
    status: PaymentMethodStatus
    created_at: datetime
