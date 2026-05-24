"""Braintree ``transaction.sale`` helpers.

Cardholder-initiated charges use a **payment method nonce** from client
``threeDSecure.verifyCard`` (or Drop-in 3DS). The server validates 3DS metadata on that
nonce, then calls ``transaction.sale`` with ``payment_method_nonce``.

Merchant-initiated transactions (MIT), established mandate, and network rules are
separate; consult your acquirer and Braintree docs for recurring or off-session charges.

**Pricing:** Whether 3DS incurs a per-authentication or bundled fee depends on your
PayPal Braintree contract and product tier; it is not encoded in this codebase—check
your pricing schedule or account manager.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog

from app.common.enums import LogEvent
from app.common.exceptions import PaymentProviderUnavailableError, ValidationError
from app.integrations.braintree.three_d_secure import validate_payment_method_nonce_three_d_secure

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class TransactionReversalResult:
    success: bool
    action: str
    original_transaction_id: str
    transaction_id: str | None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class TransactionCardSnapshot:
    card_type: str | None
    country_of_issuance: str | None


def braintree_transaction_card_snapshot(transaction: Any) -> TransactionCardSnapshot:
    card = getattr(transaction, "credit_card_details", None)
    if card is None:
        return TransactionCardSnapshot(card_type=None, country_of_issuance=None)
    card_type = str(getattr(card, "card_type", "") or "").strip() or None
    country = str(getattr(card, "country_of_issuance", "") or "").strip().upper() or None
    if country == "UNKNOWN":
        country = None
    return TransactionCardSnapshot(card_type=card_type, country_of_issuance=country)


def transaction_sale_with_payment_method_nonce(
    gateway: Any,
    *,
    amount: Decimal,
    payment_method_nonce: str,
    order_id: str | None = None,
    submit_for_settlement: bool = True,
    owner_label: str = "payment",
) -> Any:
    """Run ``transaction.sale`` with a 3DS-enriched payment-method nonce (e.g. from ``verifyCard``).

    Runs ``validate_payment_method_nonce_three_d_secure`` before the sale. The payload uses
    ``payment_method_nonce``; ``options.three_d_secure.required`` is not set (authentication
    is already on the nonce).
    """
    raw = str(payment_method_nonce).strip()
    if not raw:
        raise ValidationError("payment_method_nonce is required")
    validate_payment_method_nonce_three_d_secure(
        gateway,
        raw,
        owner_label=owner_label,
    )
    amt = f"{amount.quantize(Decimal('0.01'))}"
    options: dict[str, Any] = {"submit_for_settlement": submit_for_settlement}
    payload: dict[str, Any] = {
        "amount": amt,
        "payment_method_nonce": raw,
        "options": options,
    }
    if order_id:
        payload["order_id"] = order_id[:255]
    try:
        return gateway.transaction.sale(payload)
    except ValidationError:
        raise
    except Exception as exc:
        logger.error(
            LogEvent.BRAINTREE_TRANSACTION_SALE_ERROR,
            owner=owner_label,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise PaymentProviderUnavailableError() from None


def transaction_sale_with_payment_method_token(
    gateway: Any,
    *,
    amount: Decimal,
    payment_method_token: str,
    order_id: str | None = None,
    submit_for_settlement: bool = True,
) -> Any:
    raw_token = str(payment_method_token).strip()
    if not raw_token:
        raise ValidationError("payment_method_token is required")
    amt = f"{amount.quantize(Decimal('0.01'))}"
    payload: dict[str, Any] = {
        "amount": amt,
        "payment_method_token": raw_token,
        "options": {"submit_for_settlement": submit_for_settlement},
    }
    if order_id:
        payload["order_id"] = order_id[:255]
    return gateway.transaction.sale(payload)


def refund_or_void_transaction(
    gateway: Any,
    *,
    transaction_id: str,
    amount: Decimal | None = None,
    order_id: str | None = None,
    owner_label: str = "payment",
) -> TransactionReversalResult:
    def _money_or_none(value: Any) -> Decimal | None:
        try:
            return Decimal(str(value)).quantize(Decimal("0.01"))
        except Exception:
            return None

    raw_id = str(transaction_id).strip()
    if not raw_id:
        raise ValidationError("transaction_id is required")

    tx = gateway.transaction.find(raw_id)
    status = str(getattr(tx, "status", "") or "").strip().lower()

    if status == "voided":
        return TransactionReversalResult(
            success=False,
            action="none",
            original_transaction_id=raw_id,
            transaction_id=raw_id,
            message="Transaction is already voided and cannot be reversed again.",
        )

    if status == "settlement_declined":
        return TransactionReversalResult(
            success=False,
            action="none",
            original_transaction_id=raw_id,
            transaction_id=raw_id,
            message="Transaction settlement was declined; nothing to refund or void.",
        )

    if str(getattr(tx, "type", "") or "").strip().lower() == "credit":
        return TransactionReversalResult(
            success=False,
            action="none",
            original_transaction_id=raw_id,
            transaction_id=raw_id,
            message="Transaction is already a credit/refund transaction.",
        )

    if getattr(tx, "refunded_transaction_id", None):
        return TransactionReversalResult(
            success=False,
            action="none",
            original_transaction_id=raw_id,
            transaction_id=raw_id,
            message="Transaction is already a refund record and cannot be reversed.",
        )

    refund_id = str(getattr(tx, "refund_id", "") or "").strip()
    if refund_id:
        return TransactionReversalResult(
            success=False,
            action="none",
            original_transaction_id=raw_id,
            transaction_id=refund_id,
            message="Transaction has already been refunded.",
        )

    voidable_statuses = {
        "authorized",
        "submitted_for_settlement",
        "settlement_pending",
    }

    if status in voidable_statuses:
        tx_amount = _money_or_none(getattr(tx, "amount", None))
        requested_amount = amount.quantize(Decimal("0.01")) if amount is not None else None
        if requested_amount is not None and tx_amount is not None and requested_amount != tx_amount:
            return TransactionReversalResult(
                success=False,
                action="void",
                original_transaction_id=raw_id,
                transaction_id=raw_id,
                message=(
                    "Partial void is not supported by Braintree. "
                    "Use full amount for void or wait for settlement and refund partial."
                ),
            )
        result = gateway.transaction.void(raw_id)
        if getattr(result, "is_success", False):
            return TransactionReversalResult(
                success=True,
                action="void",
                original_transaction_id=raw_id,
                transaction_id=raw_id,
            )
        return TransactionReversalResult(
            success=False,
            action="void",
            original_transaction_id=raw_id,
            transaction_id=raw_id,
            message=str(getattr(result, "message", "") or "").strip() or None,
        )

    refundable_statuses = {
        "settling",
        "settled",
    }
    if status not in refundable_statuses:
        return TransactionReversalResult(
            success=False,
            action="none",
            original_transaction_id=raw_id,
            transaction_id=raw_id,
            message=f"Transaction status '{status or 'unknown'}' is not refundable yet.",
        )

    refund_amount = f"{amount.quantize(Decimal('0.01'))}" if amount is not None else None
    result: Any
    if order_id:
        payload: dict[str, Any] = {"order_id": str(order_id).strip()[:255]}
        if refund_amount is not None:
            payload["amount"] = refund_amount
        try:
            result = gateway.transaction.refund(raw_id, payload)
        except TypeError:
            result = gateway.transaction.refund(raw_id) if refund_amount is None else gateway.transaction.refund(raw_id, refund_amount)
    else:
        result = gateway.transaction.refund(raw_id) if refund_amount is None else gateway.transaction.refund(raw_id, refund_amount)

    if getattr(result, "is_success", False):
        tx_obj = getattr(result, "transaction", None)
        new_tx_id = str(getattr(tx_obj, "id", "") or "").strip() or None
        return TransactionReversalResult(
            success=True,
            action="refund",
            original_transaction_id=raw_id,
            transaction_id=new_tx_id,
        )

    logger.warning(
        "braintree.transaction_reversal_failed",
        owner=owner_label,
        action="refund",
        original_transaction_id=raw_id,
        message=getattr(result, "message", None),
    )
    return TransactionReversalResult(
        success=False,
        action="refund",
        original_transaction_id=raw_id,
        transaction_id=None,
        message=str(getattr(result, "message", "") or "").strip() or None,
    )
