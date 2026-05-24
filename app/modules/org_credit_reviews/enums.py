from __future__ import annotations

import enum

from app.modules.org_credit.enums import OrgCreditLedgerMovementType


class CreditReviewRiskLevel(enum.StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class CreditReviewOutcome(enum.StrEnum):
    MAINTAIN_CURRENT_TERMS = "MAINTAIN_CURRENT_TERMS"
    INCREASE_LIMIT = "INCREASE_LIMIT"
    DECREASE_LIMIT = "DECREASE_LIMIT"
    EXTEND_TERMS = "EXTEND_TERMS"
    SHORTEN_TERMS = "SHORTEN_TERMS"
    SUSPEND_ACCOUNT = "SUSPEND_ACCOUNT"
    CLOSE_ACCOUNT = "CLOSE_ACCOUNT"
    ESCALATE_TO_SENIOR_ADMIN = "ESCALATE_TO_SENIOR_ADMIN"


class CreditReviewReminderPeriod(enum.StrEnum):
    THREE_DAYS = "THREE_DAYS"
    SEVEN_DAYS = "SEVEN_DAYS"
    FOURTEEN_DAYS = "FOURTEEN_DAYS"
