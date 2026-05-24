from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.modules.org_credit_alerts.enums import (
    CreditAlertCooldownPeriod,
    CreditAlertDeliveryChannel,
    CreditAlertType,
)

DEFAULT_CONFIG: dict[CreditAlertType, dict[str, Any]] = {
    CreditAlertType.CREDIT_UTILISATION_MONITORING_WARNING: {
        "enabled": True,
        "threshold_pct": Decimal("75"),
        "cooldown_period": CreditAlertCooldownPeriod.ONE_HOUR,
        "delivery_channel": CreditAlertDeliveryChannel.BOTH,
        "auto_acknowledge": True,
    },
    CreditAlertType.CREDIT_UTILISATION_MONITORING_CRITICAL: {
        "enabled": True,
        "threshold_pct": Decimal("90"),
        "cooldown_period": CreditAlertCooldownPeriod.FIVE_MINUTES,
        "delivery_channel": CreditAlertDeliveryChannel.BOTH,
        "auto_acknowledge": True,
    },
    CreditAlertType.CREDIT_SCORE_DECREASE: {
        "enabled": False,
        "score_drop_points": 10,
        "cooldown_period": CreditAlertCooldownPeriod.TWENTY_FOUR_HOURS,
        "delivery_channel": CreditAlertDeliveryChannel.BOTH,
        "auto_acknowledge": False,
    },
    CreditAlertType.CREDIT_RATING_DOWNGRADE: {
        "enabled": True,
        "cooldown_period": CreditAlertCooldownPeriod.ONE_HOUR,
        "delivery_channel": CreditAlertDeliveryChannel.BOTH,
        "auto_acknowledge": False,
    },
    CreditAlertType.SCHEDULED_CREDIT_REVIEW_REMINDER: {
        "enabled": True,
        "reminder_days": 14,
        "cooldown_period": CreditAlertCooldownPeriod.ONE_DAY,
        "delivery_channel": CreditAlertDeliveryChannel.BOTH,
        "auto_acknowledge": False,
    },
    CreditAlertType.REVIEW_OVERDUE: {
        "enabled": True,
        "cooldown_period": CreditAlertCooldownPeriod.ONE_HOUR,
        "delivery_channel": CreditAlertDeliveryChannel.BOTH,
        "auto_acknowledge": False,
    },
    CreditAlertType.LATE_PAYMENT_BEHAVIOUR: {
        "enabled": True,
        "late_payment_count": 3,
        "cooldown_period": CreditAlertCooldownPeriod.ONE_HOUR,
        "delivery_channel": CreditAlertDeliveryChannel.BOTH,
        "auto_acknowledge": False,
    },
    CreditAlertType.ACCOUNT_ON_HOLD: {
        "enabled": True,
        "cooldown_period": CreditAlertCooldownPeriod.ONE_HOUR,
        "delivery_channel": CreditAlertDeliveryChannel.BOTH,
        "auto_acknowledge": False,
    },
    CreditAlertType.ACCOUNT_SUSPENDED: {
        "enabled": True,
        "cooldown_period": CreditAlertCooldownPeriod.ONE_HOUR,
        "delivery_channel": CreditAlertDeliveryChannel.BOTH,
        "auto_acknowledge": False,
    },
}


REVIEW_REMINDER_MIN_DAYS = 7
REVIEW_REMINDER_MAX_DAYS = 30


_HOURLY_COOLDOWNS: set[CreditAlertCooldownPeriod] = {
    CreditAlertCooldownPeriod.ONE_HOUR,
    CreditAlertCooldownPeriod.SEVEN_HOURS,
    CreditAlertCooldownPeriod.FOURTEEN_HOURS,
    CreditAlertCooldownPeriod.TWENTY_FOUR_HOURS,
}

ALLOWED_COOLDOWN_PERIODS: dict[CreditAlertType, set[CreditAlertCooldownPeriod]] = {
    CreditAlertType.CREDIT_UTILISATION_MONITORING_WARNING: _HOURLY_COOLDOWNS,
    CreditAlertType.CREDIT_UTILISATION_MONITORING_CRITICAL: {
        CreditAlertCooldownPeriod.FIVE_MINUTES,
        CreditAlertCooldownPeriod.FIFTEEN_MINUTES,
        CreditAlertCooldownPeriod.THIRTY_MINUTES,
        CreditAlertCooldownPeriod.FORTY_FIVE_MINUTES,
        CreditAlertCooldownPeriod.ONE_HOUR,
    },
    CreditAlertType.CREDIT_SCORE_DECREASE: _HOURLY_COOLDOWNS,
    CreditAlertType.CREDIT_RATING_DOWNGRADE: _HOURLY_COOLDOWNS,
    CreditAlertType.SCHEDULED_CREDIT_REVIEW_REMINDER: {
        CreditAlertCooldownPeriod.ONE_DAY,
        CreditAlertCooldownPeriod.TWO_DAYS,
        CreditAlertCooldownPeriod.THREE_DAYS,
        CreditAlertCooldownPeriod.FOUR_DAYS,
        CreditAlertCooldownPeriod.FIVE_DAYS,
        CreditAlertCooldownPeriod.SIX_DAYS,
        CreditAlertCooldownPeriod.SEVEN_DAYS,
    },
    CreditAlertType.REVIEW_OVERDUE: _HOURLY_COOLDOWNS,
    CreditAlertType.LATE_PAYMENT_BEHAVIOUR: _HOURLY_COOLDOWNS,
    CreditAlertType.ACCOUNT_ON_HOLD: _HOURLY_COOLDOWNS,
    CreditAlertType.ACCOUNT_SUSPENDED: _HOURLY_COOLDOWNS,
}
