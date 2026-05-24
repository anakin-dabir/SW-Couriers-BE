"""Braintree payment-method API result helpers (duplicates, etc.)."""

from typing import Any

from braintree.error_codes import ErrorCodes

_DUPLICATE_CARD_BT_CODES = frozenset(
    {
        ErrorCodes.CreditCard.DuplicateCardExistsForCustomer,
        ErrorCodes.CreditCard.DuplicateCardExists,
    }
)


def braintree_result_is_duplicate_payment_method(result: Any) -> bool:
    if getattr(result, "is_success", False):
        return False
    errors = getattr(result, "errors", None)
    if errors is None:
        return False
    try:
        for err in errors.deep_errors:
            if getattr(err, "code", None) in _DUPLICATE_CARD_BT_CODES:
                return True
    except (TypeError, AttributeError):
        return False
    return False
