"""Create a one-time payment method nonce from a vaulted Braintree payment method token."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog
from braintree.exceptions.not_found_error import NotFoundError as BraintreeNotFoundError

from app.common.exceptions import ValidationError

logger = structlog.get_logger()


def _bin_from_payment_method_nonce(pmn: Any) -> str | None:
    """Resolve the first six digits for ``verifyCard``.

    The Python SDK sets ``details`` from the API as a plain ``dict``, so ``details.bin`` attribute
    access never works; use mapping access. ``bin_data`` may also carry a ``bin`` in some responses.
    """
    details = getattr(pmn, "details", None)
    raw: Any = None
    if isinstance(details, Mapping):
        raw = details.get("bin")
    elif details is not None:
        raw = getattr(details, "bin", None)
    if raw is None:
        bin_data = getattr(pmn, "bin_data", None)
        if isinstance(bin_data, Mapping):
            raw = bin_data.get("bin")
        elif bin_data is not None:
            raw = getattr(bin_data, "bin", None)
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def create_nonce_from_vaulted_payment_method(gateway: Any, payment_method_token: str) -> dict[str, str | None]:
    """Call Braintree ``PaymentMethodNonce: Create`` for a vault token.

    Intended for checkout: the client passes ``nonce`` and ``bin`` into ``threeDSecure.verifyCard``
    with the real order amount, then sends the returned nonce to ``transaction.sale``.

    Returns:
        ``nonce`` and ``bin`` (when Braintree exposes it on the payment method nonce ``details``).

    Raises:
        BraintreeNotFoundError: Vault token no longer exists in Braintree.
        ValidationError: Braintree returned a non-success result.
    """
    try:
        try:
            result = gateway.payment_method_nonce.create(payment_method_token)
        except TypeError:
            result = gateway.payment_method_nonce.create({"payment_method_token": payment_method_token})
    except BraintreeNotFoundError:
        logger.warning("braintree.payment_method_nonce_create_token_not_found")
        raise ValidationError("Card is no longer valid. Remove it and add your card again.") from None
    if not getattr(result, "is_success", False):
        logger.error(
            "braintree.payment_method_nonce_create_failed",
            message=getattr(result, "message", None),
        )
        raise ValidationError("Could not start card verification. Try again.")

    logger.info("braintree.payment_method_nonce_create_success")
    pmn = getattr(result, "payment_method_nonce", None)
    if pmn is None:
        logger.error("braintree.payment_method_nonce_create_missing_payload")
        raise ValidationError("Could not start card verification. Try again.")
    raw_nonce = getattr(pmn, "nonce", None)
    if not raw_nonce:
        logger.error("braintree.payment_method_nonce_create_missing_nonce")
        raise ValidationError("Could not start card verification. Try again.")
    bin_val = _bin_from_payment_method_nonce(pmn)
    return {"nonce": str(raw_nonce), "bin": bin_val}
