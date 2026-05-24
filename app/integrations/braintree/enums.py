from __future__ import annotations

import enum


class BraintreeTransactionStatus(enum.StrEnum):
    AUTHORIZED = "AUTHORIZED"
    SUBMITTED_FOR_SETTLEMENT = "SUBMITTED_FOR_SETTLEMENT"
    SETTLEMENT_PENDING = "SETTLEMENT_PENDING"
    SETTLING = "SETTLING"
    SETTLED = "SETTLED"
    SETTLEMENT_DECLINED = "SETTLEMENT_DECLINED"
    VOIDED = "VOIDED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    REFUND_SUBMITTED = "REFUND_SUBMITTED"


_BRAINTREE_STATUS_MAP: dict[str, BraintreeTransactionStatus] = {
    "authorized": BraintreeTransactionStatus.AUTHORIZED,
    "submitted_for_settlement": BraintreeTransactionStatus.SUBMITTED_FOR_SETTLEMENT,
    "settlement_pending": BraintreeTransactionStatus.SETTLEMENT_PENDING,
    "settling": BraintreeTransactionStatus.SETTLING,
    "settled": BraintreeTransactionStatus.SETTLED,
    "settlement_declined": BraintreeTransactionStatus.SETTLEMENT_DECLINED,
    "voided": BraintreeTransactionStatus.VOIDED,
    "failed": BraintreeTransactionStatus.FAILED,
    "retrying": BraintreeTransactionStatus.RETRYING,
    "refund_submitted": BraintreeTransactionStatus.REFUND_SUBMITTED,
}


class BraintreeDisputeStatus(enum.StrEnum):
    OPEN = "OPEN"
    DISPUTED = "DISPUTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    WON = "WON"
    LOST = "LOST"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"
    AUTO_ACCEPTED = "AUTO_ACCEPTED"


_BRAINTREE_DISPUTE_STATUS_MAP: dict[str, BraintreeDisputeStatus] = {
    "open": BraintreeDisputeStatus.OPEN,
    "disputed": BraintreeDisputeStatus.DISPUTED,
    "under_review": BraintreeDisputeStatus.UNDER_REVIEW,
    "won": BraintreeDisputeStatus.WON,
    "lost": BraintreeDisputeStatus.LOST,
    "accepted": BraintreeDisputeStatus.ACCEPTED,
    "expired": BraintreeDisputeStatus.EXPIRED,
    "auto_accepted": BraintreeDisputeStatus.AUTO_ACCEPTED,
}


def normalize_braintree_status(status: str | None) -> str | None:
    raw = str(status or "").strip()
    if not raw:
        return None
    key = raw.lower()
    mapped = _BRAINTREE_STATUS_MAP.get(key)
    if mapped is not None:
        return mapped.value
    return raw.upper().replace(" ", "_")


def normalize_braintree_dispute_status(status: str | None) -> str | None:
    raw = str(status or "").strip()
    if not raw:
        return None
    key = raw.lower()
    mapped = _BRAINTREE_DISPUTE_STATUS_MAP.get(key)
    if mapped is not None:
        return mapped.value
    return raw.upper().replace(" ", "_")
