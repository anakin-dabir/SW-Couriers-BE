"""Server-side checks for 3D Secure on Braintree payment-method nonces (vault, charge).

Uses ``payment_method_nonce.find`` so decisions match Braintree-documented
``three_d_secure_info`` (status, liability shift). The client must run 3DS (e.g. ``verifyCard``)
before sending the nonce for vaulting or before ``transaction.sale`` with that nonce.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from braintree.exceptions.not_found_error import NotFoundError as BraintreeNotFoundError

from app.common.enums import ErrorCode
from app.common.exceptions import ValidationError
from app.core.config import settings

logger = structlog.get_logger()

_THREE_DS_EXPLICIT_FAILURE_STATUSES = frozenset(
    {
        "authenticate_error",
        "authenticate_failed",
        "authenticate_failed_acs_error",
        "authenticate_frictionless_failed",
        "authenticate_rejected",
        "authenticate_unable_to_authenticate",
        "challenge_required",
        "lookup_card_error",
        "lookup_server_error",
    }
)

_THREE_DS_VAULT_OK_WITHOUT_LIABILITY_SHIFT = frozenset(
    {
        "authentication_unavailable",
        "data_only_successful",
        "exemption_low_value_successful",
        "exemption_tra_successful",
        "lookup_bypassed",
        "lookup_error",
        "lookup_failed_acs_error",
        "lookup_not_enrolled",
        "mpi_server_error",
        "skipped_due_to_rule",
        "unsupported_account_type",
        "unsupported_card",
        "unsupported_three_d_secure_version",
    }
)


def _payment_detail(reason: str, message: str) -> list[dict[str, Any]]:
    return [{"field": "payment_method", "type": reason, "message": message}]


def _braintree_bool(val: Any) -> bool:
    if val is True:
        return True
    if val is False or val is None:
        return False
    if isinstance(val, str):
        return val.strip().lower() in {"true", "1", "yes"}
    return bool(val)


def payment_method_nonce_log_summary(pmn: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    pmn_type = getattr(pmn, "type", None)
    if pmn_type is not None:
        summary["pmn_type"] = str(pmn_type)
    details = getattr(pmn, "details", None)
    if details is not None:
        card_type = getattr(details, "card_type", None)
        if card_type is not None:
            summary["pmn_card_type"] = str(card_type)
    return summary


def three_d_secure_log_fields(info: Any) -> dict[str, Any]:
    raw_status = getattr(info, "status", None)
    status_val: str | None = None
    if raw_status is not None and str(raw_status).strip():
        status_val = str(raw_status).strip()
    raw_enrolled = getattr(info, "enrolled", None)
    enrolled_val: str | None = None
    if raw_enrolled is not None and str(raw_enrolled).strip():
        enrolled_val = str(raw_enrolled).strip()
    return {
        "three_ds_status": status_val,
        "three_ds_enrolled": enrolled_val,
        "liability_shifted": _braintree_bool(getattr(info, "liability_shifted", None)),
        "liability_shift_possible": _braintree_bool(getattr(info, "liability_shift_possible", None)),
    }


def validate_payment_method_nonce_three_d_secure(
    gateway: Any,
    nonce: str,
    *,
    owner_label: str,
) -> None:
    """Load a nonce via ``payment_method_nonce.find`` and enforce acceptable 3DS outcome.

    Used after client ``verifyCard`` before vaulting (when configured) and always before
    ``transaction.sale`` with a payment-method nonce.

    Raises:
        ValidationError: Nonce unknown or 3DS outcome not acceptable.
    """
    nonce_len = len(nonce)
    try:
        pmn = gateway.payment_method_nonce.find(nonce)
    except BraintreeNotFoundError:
        logger.warning(
            "braintree.payment_method_nonce_not_found",
            owner=owner_label,
            nonce_len=nonce_len,
        )
        raise ValidationError(
            "Card session expired. Try again.",
            details=_payment_detail("payment_nonce_invalid", "Card session expired. Try again."),
            code=ErrorCode.PAYMENT_NONCE_INVALID,
        ) from None
    info = getattr(pmn, "three_d_secure_info", None)

    if info is None:
        logger.warning(
            "braintree.three_d_secure_missing",
            owner=owner_label,
            nonce_len=nonce_len,
            **payment_method_nonce_log_summary(pmn),
        )
        raise ValidationError(
            "Bank verification failed. Try again.",
            details=_payment_detail("bank_authentication_required", "Bank verification failed. Try again."),
            code=ErrorCode.BANK_AUTHENTICATION_REQUIRED,
        )
    tds = three_d_secure_log_fields(info)
    logger.info(
        "braintree.three_d_secure_nonce_tds",
        owner=owner_label,
        nonce_len=nonce_len,
        three_ds=tds,
        three_ds_json=json.dumps(tds, default=str, sort_keys=True),
    )
    st = tds.get("three_ds_status")
    status_key = st.lower() if st else ""
    if status_key and status_key in _THREE_DS_EXPLICIT_FAILURE_STATUSES:
        logger.warning(
            "braintree.three_d_secure_status_failed",
            owner=owner_label,
            nonce_len=nonce_len,
            **tds,
        )
        raise ValidationError(
            "Bank verification failed. Try again.",
            details=_payment_detail("bank_verification_failed", "Bank verification failed. Try again."),
            code=ErrorCode.BANK_VERIFICATION_FAILED,
        )
    if status_key and status_key in _THREE_DS_VAULT_OK_WITHOUT_LIABILITY_SHIFT:
        logger.info(
            "braintree.three_d_secure_nonce_verified",
            owner=owner_label,
            nonce_len=nonce_len,
            three_ds_outcome="braintree_allows_vault_without_liability_shift",
            **tds,
        )
        return
    if tds["liability_shift_possible"] and not tds["liability_shifted"]:
        logger.warning(
            "braintree.three_d_secure_liability_not_shifted",
            owner=owner_label,
            nonce_len=nonce_len,
            **tds,
        )
        raise ValidationError(
            "Bank verification failed. Try again.",
            details=_payment_detail("bank_verification_failed", "Bank verification failed. Try again."),
            code=ErrorCode.BANK_VERIFICATION_FAILED,
        )
    logger.info(
        "braintree.three_d_secure_nonce_verified",
        owner=owner_label,
        nonce_len=nonce_len,
        **tds,
    )


def require_three_d_secure_nonce_for_vault(
    gateway: Any,
    nonce: str,
    *,
    owner_label: str,
) -> None:
    """Validate that a nonce carries acceptable 3DS metadata before vaulting.

    Loads the nonce via ``gateway.payment_method_nonce.find``. When
    ``BRAINTREE_REQUIRE_THREE_D_SECURE_FOR_VAULT`` is false, returns without calling Braintree.

    Raises:
        ValidationError: Nonce missing, unknown, or 3DS outcome not acceptable for vault.
    """
    if not settings.BRAINTREE_REQUIRE_THREE_D_SECURE_FOR_VAULT:
        return
    validate_payment_method_nonce_three_d_secure(gateway, nonce, owner_label=owner_label)
