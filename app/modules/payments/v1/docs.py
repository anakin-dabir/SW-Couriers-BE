from __future__ import annotations

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry

GET_CLIENT_TOKEN = create_doc_entry(
    "Get Braintree client token",
    {
        200: success_entry(
            "Client token for Hosted Fields / Drop-in initialization",
            data={"client_token": "sandbox_client_token_..."},
        ),
        401: error_401_entry(),
    },
    description=(
        "``GET /v1/payment-methods/cards/braintree-client-token``. Returns a short-lived token used by the frontend to "
        "initialize Braintree Hosted Fields or Drop-in UI. Card PAN/CVV never passes through this API."
    ),
)

CREATE_PAYMENT_METHOD = create_doc_entry(
    "Save a new card",
    {
        201: success_entry(
            "Card saved successfully",
            message="Card saved successfully",
            data={
                "id": "...",
                "card_type": "VISA",
                "last_four": "4242",
                "expiry_month": 12,
                "expiry_year": 2029,
                "cardholder_name": "Shift Opus",
                "is_default": True,
                "status": "ACTIVE",
                "created_at": "2026-04-01T10:00:00Z",
            },
        ),
        401: error_401_entry(),
        409: error_entry(
            "Duplicate card",
            code="CONFLICT",
            message="This card is already saved.",
        ),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Card verification failed"),
    },
    description=(
        "``POST /v1/payment-methods/cards``. Creates a vaulted card in Braintree using a client-side "
        "nonce. The server loads the nonce via Braintree to ensure 3D Secure completed (liability shifted) before vaulting. "
        "Only masked card data (type, last four, expiry) is persisted in the ``credit_cards`` table."
    ),
)

LIST_PAYMENT_METHODS = create_doc_entry(
    "List saved cards",
    {
        200: success_entry(
            "Saved cards for current owner",
            data=[
                {
                    "id": "...",
                    "card_type": "VISA",
                    "last_four": "4242",
                    "expiry_month": 12,
                    "expiry_year": 2029,
                    "cardholder_name": "Shift Opus",
                    "is_default": True,
                    "status": "ACTIVE",
                    "created_at": "2026-04-01T10:00:00Z",
                }
            ],
        ),
        401: error_401_entry(),
    },
    description="``GET /v1/payment-methods/cards``. Returns all active cards for the current owner; default first.",
)

GET_PAYMENT_METHOD = create_doc_entry(
    "Get card details",
    {
        200: success_entry(
            "Payment method details",
            data={
                "id": "...",
                "card_type": "MASTERCARD",
                "last_four": "4444",
                "expiry_month": 8,
                "expiry_year": 2028,
                "cardholder_name": "Shift Opus",
                "is_default": False,
                "status": "ACTIVE",
                "created_at": "2026-04-01T10:00:00Z",
            },
        ),
        401: error_401_entry(),
        404: error_entry("Card not found", code="NOT_FOUND", message="credit_card with id '...' not found"),
    },
)

SET_DEFAULT_PAYMENT_METHOD = create_doc_entry(
    "Set card as default",
    {
        200: success_entry(
            "Default card updated",
            message="Default card updated",
            data={
                "id": "...",
                "card_type": "VISA",
                "last_four": "4242",
                "expiry_month": 12,
                "expiry_year": 2029,
                "cardholder_name": "Shift Opus",
                "is_default": True,
                "status": "ACTIVE",
                "created_at": "2026-04-01T10:00:00Z",
            },
        ),
        401: error_401_entry(),
        404: error_entry("Card not found", code="NOT_FOUND", message="credit_card with id '...' not found"),
    },
)

PREPARE_CHECKOUT_NONCE = create_doc_entry(
    "Prepare checkout nonce for 3DS",
    {
        200: success_entry(
            "Nonce for verifyCard",
            data={"nonce": "tokenization_key_nonce_abc", "bin": "411111"},
        ),
        401: error_401_entry(),
        404: error_entry("Card not found", code="NOT_FOUND", message="credit_card with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Could not start card verification. Try again.",
        ),
    },
    description=(
        "``POST /v1/payment-methods/cards/prepare-payment``. Body: ``card_id`` of a saved card. "
        "Returns a one-time nonce (and BIN when available) so the client can run Braintree ``threeDSecure.verifyCard`` "
        "with the **actual order amount**, then charge with the nonce returned from verify."
    ),
)

DELETE_PAYMENT_METHOD = create_doc_entry(
    "Delete a card",
    {
        200: success_entry("Card removed", message="Card removed"),
        401: error_401_entry(),
        404: error_entry("Card not found", code="NOT_FOUND", message="credit_card with id '...' not found"),
    },
    description="Deletes the card from Braintree Vault and removes the local saved payment method record.",
)

DEV_CREATE_RAW_CARD = create_doc_entry(
    "DEV ONLY: create card from raw fields",
    {
        200: success_entry(
            "Raw card vaulted and saved",
            message="Dev raw card saved",
            data={
                "id": "...",
                "card_type": "VISA",
                "last_four": "1111",
                "expiry_month": 12,
                "expiry_year": 2029,
                "cardholder_name": "Test User",
                "is_default": True,
                "status": "ACTIVE",
                "created_at": "2026-04-01T10:00:00Z",
            },
        ),
        401: error_401_entry(),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="This endpoint is available only in development/test with sandbox"),
    },
    description=(
        "Testing-only endpoint. Sends raw card fields backend->Braintree (sandbox) and saves the vaulted method. "
        "Do not use in production; normal SAQ-A flow is frontend tokenization and nonce."
    ),
)

DEV_CHARGE_SAVED_CARD = create_doc_entry(
    "DEV ONLY: charge saved card",
    {
        200: success_entry(
            "Saved card charged",
            message="Dev saved-card charge attempted",
            data={
                "success": True,
                "braintree_transaction_id": "the_tx_id",
                "payment_status": "paid",
                "processor_message": None,
                "amount": "15.50",
            },
        ),
        401: error_401_entry(),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="This endpoint is available only in development/test with sandbox"),
    },
    description=(
        "Testing-only endpoint to run a booking-style charge. Requires ``credit_card_id``, ``amount``, and "
        "``nonce`` from Braintree.js ``threeDSecure.verifyCard`` for that card and amount. "
        "The API validates 3DS on the nonce then calls ``transaction.sale`` with ``payment_method_nonce`` "
        "and ``submit_for_settlement``."
    ),
)
