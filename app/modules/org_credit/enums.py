from __future__ import annotations

import enum


class OrgCreditAccountStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class OrgCreditLedgerMovementType(enum.StrEnum):
    """Money-movement events on the credit wallet.

    Every entry represents an actual change to ``used_credit`` on the
    account. Status transitions, limit changes and terms changes live in
    their own dedicated history tables and are not replayed into the
    ledger.
    """

    CONSUME = "CONSUME"
    REPAY = "REPAY"
    MANUAL_ADJUST_USED = "MANUAL_ADJUST_USED"


class OrgCreditLedgerSourceType(enum.StrEnum):
    ORDER = "ORDER"
    INVOICE = "INVOICE"
    PAYMENT = "PAYMENT"
    MANUAL = "MANUAL"
    SYSTEM = "SYSTEM"


class OrgCreditAdjustmentReason(enum.StrEnum):
    COMPLIANCE = "COMPLIANCE"
    GOODWILL = "GOODWILL"
    DATA_CORRECTION = "DATA_CORRECTION"
    OTHER = "OTHER"


class OrgCreditReviewFrequency(enum.StrEnum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    SEMI_ANNUAL = "SEMI_ANNUAL"
    ANNUAL = "ANNUAL"


class OrgCreditInvestigationStatus(enum.StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class HoldReasonCategory(enum.StrEnum):
    INVESTIGATION_PENDING = "INVESTIGATION_PENDING"
    CLIENT_REQUEST = "CLIENT_REQUEST"
    PAYMENT_DISPUTE = "PAYMENT_DISPUTE"
    RISK_CONCERN = "RISK_CONCERN"
    OTHER = "OTHER"


class CloseAccountReason(enum.StrEnum):
    CLIENT_SWITCHED_TO_PREPAID = "CLIENT_SWITCHED_TO_PREPAID"
    BUSINESS_RELATIONSHIP_TERMINATED = "BUSINESS_RELATIONSHIP_TERMINATED"
    ACCOUNT_SETTLED_AFTER_SUSPENSION = "ACCOUNT_SETTLED_AFTER_SUSPENSION"
    RISK_OR_COMPLIANCE_CONCERNS = "RISK_OR_COMPLIANCE_CONCERNS"
    OTHER = "OTHER"


class InternalCreditScoreBand(enum.StrEnum):
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    FAIR = "FAIR"
    POOR = "POOR"
    VERY_POOR = "VERY_POOR"


def internal_credit_score_band(score: int) -> InternalCreditScoreBand:
    s = min(100, max(0, score))
    if s >= 80:
        return InternalCreditScoreBand.EXCELLENT
    if s >= 60:
        return InternalCreditScoreBand.GOOD
    if s >= 40:
        return InternalCreditScoreBand.FAIR
    if s >= 20:
        return InternalCreditScoreBand.POOR
    return InternalCreditScoreBand.VERY_POOR
