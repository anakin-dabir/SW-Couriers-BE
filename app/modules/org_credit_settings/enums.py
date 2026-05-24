from __future__ import annotations

import enum


class CooldownResolutionSource(enum.StrEnum):
    ORG = "ORG"
    GLOBAL = "GLOBAL"
    DEFAULT = "DEFAULT"


class ScheduledCreditSettingStatus(enum.StrEnum):
    SCHEDULED = "SCHEDULED"
    APPLIED = "APPLIED"


class CreditLimitAdjustmentReason(enum.StrEnum):
    BUSINESS_GROWTH = "BUSINESS_GROWTH"
    SEASONAL_DEMAND = "SEASONAL_DEMAND"
    CLIENT_REQUEST = "CLIENT_REQUEST"
    RISK_REDUCTION = "RISK_REDUCTION"
    PAYMENT_IMPROVEMENT = "PAYMENT_IMPROVEMENT"
    SYSTEM_RECOMMENDATION = "SYSTEM_RECOMMENDATION"
    OTHER = "OTHER"
