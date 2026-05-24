"""Saved credit card and Braintree client-token routes.

## Integration guide for frontend developers

### Flow (PCI SAQ-A compliant -- card numbers never touch your server):

1. **GET /payment-methods/cards/braintree-client-token** -- get a Braintree client token
2. **Frontend** initializes Braintree Hosted Fields or Drop-in UI with the token
3. **User** fills in card details in Braintree's hosted iframe
4. **Frontend** calls `braintree.tokenize()` to get a payment method **nonce**
5. **POST /payment-methods/cards** -- send the nonce and optional cardholder name to your server
6. **Backend** creates the card in Braintree Vault and stores only masked info (last 4, type, expiry)

### Paying with a saved card (3DS + real amount):
1. **POST /payment-methods/cards/prepare-payment** with ``card_id`` -- server returns a fresh nonce (and BIN if present)
2. **Frontend** runs ``threeDSecure.verifyCard`` using that nonce, **order amount**, and BIN as needed
3. Submit payment using the **nonce returned from verifyCard** (not the prepare-payment nonce)

### Card management:
- **GET /payment-methods/cards** -- list saved cards
- **POST /payment-methods/cards/prepare-payment** -- get a one-time nonce for a saved card (for client ``threeDSecure.verifyCard`` with order amount)
- **PATCH /payment-methods/cards/{card_id}/default** -- set a card as default
- **DELETE /payment-methods/cards/{card_id}** -- remove a card (also removes from Braintree Vault)

### Ownership:
- **B2B users** (CUSTOMER_B2B): cards belong to the **organization**. Any user in the org can manage them.
- **B2C users** (CUSTOMER_B2C): cards belong to the **individual user**.

### What goes to Braintree directly (frontend JS SDK):
- Card number, CVV, expiry -- tokenized client-side by Braintree Hosted Fields
- Your server only ever receives the resulting **nonce** string

### What goes to your server:
- The **nonce** (a one-time-use token representing the card)
- Optional **cardholder name** (not sensitive PCI data)

### Dev testing note:
- For local sandbox testing only, there are dev-only routes under `cards/dev/...` that can send raw card data or charge a saved card with a **verifyCard** nonce.
- Never use those in production. Production and staging should use frontend tokenization + nonce only.
"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, status

from app.common.deps import Allowed, AuditCtxDep, AuthUser, UserRole
from app.common.response import ok
from app.common.schemas import MessageResponse, SuccessResponse
from app.modules.payments.service import CreditCardOwner, PaymentService
from app.modules.payments.v1.docs import (
    CREATE_PAYMENT_METHOD,
    DELETE_PAYMENT_METHOD,
    DEV_CHARGE_SAVED_CARD,
    DEV_CREATE_RAW_CARD,
    GET_CLIENT_TOKEN,
    GET_PAYMENT_METHOD,
    LIST_PAYMENT_METHODS,
    PREPARE_CHECKOUT_NONCE,
    SET_DEFAULT_PAYMENT_METHOD,
)
from app.modules.payments.v1.schemas import (
    ClientTokenResponse,
    CreatePaymentMethodRequest,
    DevChargeSavedCardRequest,
    DevChargeSavedCardResponse,
    DevCreateRawCardRequest,
    DevReverseTransactionRequest,
    DevReverseTransactionResponse,
    PaymentMethodResponse,
    PreparePaymentNonceRequest,
    PreparePaymentNonceResponse,
)

logger = structlog.get_logger()

router = APIRouter()

CustomerUserDep = Annotated[AuthUser, Allowed(UserRole.ADMIN, UserRole.CUSTOMER_B2B, UserRole.CUSTOMER_B2C)]
DevUserDep = Annotated[AuthUser, Allowed(UserRole.ADMIN, UserRole.CUSTOMER_B2B, UserRole.CUSTOMER_B2C)]
PaymentServiceDep = Annotated[PaymentService, Depends(PaymentService.dep)]


def _resolve_owner(user: AuthUser) -> CreditCardOwner:
    if user.role == UserRole.CUSTOMER_B2B:
        if not user.organization_id:
            from app.common.exceptions import ValidationError

            raise ValidationError("Organization context required for B2B payment operations")
        return CreditCardOwner(organization_id=user.organization_id)
    return CreditCardOwner(user_id=user.id)


@router.get(
    "/cards/braintree-client-token",
    response_model=SuccessResponse[ClientTokenResponse],
    **GET_CLIENT_TOKEN,
)
async def get_braintree_client_token(
    user: CustomerUserDep,
    svc: PaymentServiceDep,
) -> dict:
    owner = _resolve_owner(user)
    token = await svc.generate_client_token(owner)
    return ok(ClientTokenResponse(client_token=token))


@router.post(
    "/cards",
    response_model=SuccessResponse[PaymentMethodResponse],
    status_code=status.HTTP_201_CREATED,
    **CREATE_PAYMENT_METHOD,
)
async def create_payment_method(
    user: CustomerUserDep,
    svc: PaymentServiceDep,
    ctx: AuditCtxDep,
    data: CreatePaymentMethodRequest,
) -> dict:
    owner = _resolve_owner(user)
    result = await svc.create_payment_method(
        owner=owner,
        nonce=data.nonce,
        ctx=ctx,
        cardholder_name=data.cardholder_name,
        set_as_default=data.set_as_default,
    )
    return ok(result, message="Card saved successfully")


@router.get(
    "/cards",
    response_model=SuccessResponse[list[PaymentMethodResponse]],
    **LIST_PAYMENT_METHODS,
)
async def list_payment_methods(
    user: CustomerUserDep,
    svc: PaymentServiceDep,
) -> dict:
    owner = _resolve_owner(user)
    cards = await svc.list_payment_methods(owner)
    return ok(cards)


@router.post(
    "/cards/dev/raw-card",
    response_model=SuccessResponse[PaymentMethodResponse],
    **DEV_CREATE_RAW_CARD,
)
async def dev_create_raw_card(
    data: DevCreateRawCardRequest,
    user: DevUserDep,
    svc: PaymentServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    owner = _resolve_owner(user)
    card = await svc.create_payment_method_from_raw_card_for_dev(
        owner=owner,
        ctx=ctx,
        card_number=data.card_number,
        expiry_month=data.expiry_month,
        expiry_year=data.expiry_year,
        cvv=data.cvv,
        cardholder_name=data.cardholder_name,
        set_as_default=data.set_as_default,
    )
    return ok(card, message="Dev raw card saved")


@router.post(
    "/cards/dev/charge-saved",
    response_model=SuccessResponse[DevChargeSavedCardResponse],
    **DEV_CHARGE_SAVED_CARD,
)
async def dev_charge_saved_card(
    data: DevChargeSavedCardRequest,
    user: DevUserDep,
    svc: PaymentServiceDep,
) -> dict:
    svc._ensure_dev_card_testing_allowed()
    owner = _resolve_owner(user)
    result = await svc.charge_saved_card_for_booking(
        owner,
        credit_card_id=data.credit_card_id,
        amount=data.amount,
        order_id=data.order_id,
        verified_payment_method_nonce=data.nonce,
    )
    return ok(
        DevChargeSavedCardResponse(
            success=result.success,
            braintree_transaction_id=result.braintree_transaction_id,
            payment_status=result.payment_status,
            processor_message=result.processor_message,
            amount=data.amount,
            transaction_fee=result.transaction_fee,
            expected_fee_amount=result.expected_fee_amount,
            fee_card_brand=result.fee_card_brand,
            country_of_issuance=result.country_of_issuance,
        ),
        message="Dev saved-card charge attempted",
    )


@router.post(
    "/cards/dev/reverse-transaction",
    response_model=SuccessResponse[DevReverseTransactionResponse],
)
async def dev_reverse_transaction(
    data: DevReverseTransactionRequest,
    user: DevUserDep,
    svc: PaymentServiceDep,
) -> dict:
    svc._ensure_dev_card_testing_allowed()
    _ = user
    result = await svc.reverse_transaction_for_dev(
        transaction_id=data.transaction_id,
        amount=data.amount,
    )
    return ok(DevReverseTransactionResponse(**result), message="Dev transaction reversal attempted")


@router.post(
    "/cards/prepare-payment",
    response_model=SuccessResponse[PreparePaymentNonceResponse],
    **PREPARE_CHECKOUT_NONCE,
)
async def post_prepare_checkout_nonce(
    user: CustomerUserDep,
    svc: PaymentServiceDep,
    ctx: AuditCtxDep,
    data: PreparePaymentNonceRequest,
) -> dict:
    owner = _resolve_owner(user)
    payload = await svc.prepare_checkout_nonce(owner, data.card_id, ctx)
    return ok(payload)


@router.get(
    "/cards/{card_id}",
    response_model=SuccessResponse[PaymentMethodResponse],
    **GET_PAYMENT_METHOD,
)
async def get_payment_method(
    card_id: str,
    user: CustomerUserDep,
    svc: PaymentServiceDep,
) -> dict:
    owner = _resolve_owner(user)
    card = await svc.get_payment_method(owner, card_id)
    return ok(card)


@router.patch(
    "/cards/{card_id}/default",
    response_model=SuccessResponse[PaymentMethodResponse],
    **SET_DEFAULT_PAYMENT_METHOD,
)
async def set_default_card(
    card_id: str,
    user: CustomerUserDep,
    svc: PaymentServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    owner = _resolve_owner(user)
    card = await svc.set_default(owner, card_id, ctx)
    return ok(card, message="Default card updated")


@router.delete(
    "/cards/{card_id}",
    response_model=SuccessResponse[MessageResponse],
    **DELETE_PAYMENT_METHOD,
)
async def delete_payment_method(
    card_id: str,
    user: CustomerUserDep,
    svc: PaymentServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    owner = _resolve_owner(user)
    await svc.delete_payment_method(owner, card_id, ctx)
    return ok(MessageResponse(message="Card removed"))
