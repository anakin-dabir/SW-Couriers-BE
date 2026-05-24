from __future__ import annotations

import enum


class CreditAlertType(enum.StrEnum):
    """Alert types shown on the Credit Alerts tab. One config row per (org, type)."""

    CREDIT_UTILISATION_MONITORING_WARNING = "CREDIT_UTILISATION_MONITORING_WARNING"
    CREDIT_UTILISATION_MONITORING_CRITICAL = "CREDIT_UTILISATION_MONITORING_CRITICAL"
    CREDIT_SCORE_DECREASE = "CREDIT_SCORE_DECREASE"
    CREDIT_RATING_DOWNGRADE = "CREDIT_RATING_DOWNGRADE"
    SCHEDULED_CREDIT_REVIEW_REMINDER = "SCHEDULED_CREDIT_REVIEW_REMINDER"
    REVIEW_OVERDUE = "REVIEW_OVERDUE"
    LATE_PAYMENT_BEHAVIOUR = "LATE_PAYMENT_BEHAVIOUR"
    ACCOUNT_ON_HOLD = "ACCOUNT_ON_HOLD"
    ACCOUNT_SUSPENDED = "ACCOUNT_SUSPENDED"


class CreditAlertSeverity(enum.StrEnum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class CreditAlertStatus(enum.StrEnum):
    """Lifecycle of a fired alert row."""

    ACTIVE = "ACTIVE"
    SNOOZED = "SNOOZED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    AUTO_ACKNOWLEDGED = "AUTO_ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class CreditAlertSnoozeDuration(enum.StrEnum):
    ONE_HOUR = "ONE_HOUR"
    FOUR_HOURS = "FOUR_HOURS"
    TWENTY_FOUR_HOURS = "TWENTY_FOUR_HOURS"
    SEVEN_DAYS = "SEVEN_DAYS"


class CreditAlertCooldownPeriod(enum.StrEnum):
    """Cooldown window after a fired alert before the same (org, type) can fire again."""

    FIVE_MINUTES = "FIVE_MINUTES"
    FIFTEEN_MINUTES = "FIFTEEN_MINUTES"
    THIRTY_MINUTES = "THIRTY_MINUTES"
    FORTY_FIVE_MINUTES = "FORTY_FIVE_MINUTES"
    ONE_HOUR = "ONE_HOUR"
    SEVEN_HOURS = "SEVEN_HOURS"
    FOURTEEN_HOURS = "FOURTEEN_HOURS"
    TWENTY_FOUR_HOURS = "TWENTY_FOUR_HOURS"
    ONE_DAY = "ONE_DAY"
    TWO_DAYS = "TWO_DAYS"
    THREE_DAYS = "THREE_DAYS"
    FOUR_DAYS = "FOUR_DAYS"
    FIVE_DAYS = "FIVE_DAYS"
    SIX_DAYS = "SIX_DAYS"
    SEVEN_DAYS = "SEVEN_DAYS"


class CreditAlertDeliveryChannel(enum.StrEnum):
    BOTH = "BOTH"
    EMAIL_ONLY = "EMAIL_ONLY"
    IN_APP_ONLY = "IN_APP_ONLY"


SNOOZE_DURATION_HOURS: dict[CreditAlertSnoozeDuration, int] = {
    CreditAlertSnoozeDuration.ONE_HOUR: 1,
    CreditAlertSnoozeDuration.FOUR_HOURS: 4,
    CreditAlertSnoozeDuration.TWENTY_FOUR_HOURS: 24,
    CreditAlertSnoozeDuration.SEVEN_DAYS: 24 * 7,
}


COOLDOWN_MINUTES: dict[CreditAlertCooldownPeriod, int] = {
    CreditAlertCooldownPeriod.FIVE_MINUTES: 5,
    CreditAlertCooldownPeriod.FIFTEEN_MINUTES: 15,
    CreditAlertCooldownPeriod.THIRTY_MINUTES: 30,
    CreditAlertCooldownPeriod.FORTY_FIVE_MINUTES: 45,
    CreditAlertCooldownPeriod.ONE_HOUR: 60,
    CreditAlertCooldownPeriod.SEVEN_HOURS: 60 * 7,
    CreditAlertCooldownPeriod.FOURTEEN_HOURS: 60 * 14,
    CreditAlertCooldownPeriod.TWENTY_FOUR_HOURS: 60 * 24,
    CreditAlertCooldownPeriod.ONE_DAY: 60 * 24,
    CreditAlertCooldownPeriod.TWO_DAYS: 60 * 24 * 2,
    CreditAlertCooldownPeriod.THREE_DAYS: 60 * 24 * 3,
    CreditAlertCooldownPeriod.FOUR_DAYS: 60 * 24 * 4,
    CreditAlertCooldownPeriod.FIVE_DAYS: 60 * 24 * 5,
    CreditAlertCooldownPeriod.SIX_DAYS: 60 * 24 * 6,
    CreditAlertCooldownPeriod.SEVEN_DAYS: 60 * 24 * 7,
}
