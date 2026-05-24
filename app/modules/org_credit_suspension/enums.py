"""Enums for per-organisation credit & suspension configuration."""

import enum


class SuspensionConditionType(str, enum.Enum):
    INVOICE_OVERDUE_DAYS = "INVOICE_OVERDUE_DAYS"
    TOTAL_OVERDUE_AMOUNT = "TOTAL_OVERDUE_AMOUNT"
    CREDIT_UTILIZATION = "CREDIT_UTILIZATION"
    CREDIT_NOT_CLEARED_AFTER_DUE_DATE = "CREDIT_NOT_CLEARED_AFTER_DUE_DATE"
