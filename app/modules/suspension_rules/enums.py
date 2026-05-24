"""Enums for account suspension rule configuration."""

import enum


class SuspensionRuleStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class SuspensionPrimaryTrigger(enum.StrEnum):
    OVERDUE_DAYS = "OVERDUE_DAYS"
    OVERDUE_AMOUNT = "OVERDUE_AMOUNT"
    OVERDUE_DAYS_AND_AMOUNT = "OVERDUE_DAYS_AND_AMOUNT"
    CREDIT_UTILISATION_PERCENT = "CREDIT_UTILISATION_PERCENT"
    CREDIT_NOT_CLEARED_AFTER_CLEARING_DATE = "CREDIT_NOT_CLEARED_AFTER_CLEARING_DATE"


class SuspensionLogic(enum.StrEnum):
    """How additional conditions are combined with the primary trigger."""

    SINGLE = "SINGLE"
    AND = "AND"


class SuspensionType(enum.StrEnum):
    """When to apply the suspension after conditions are met."""

    IMMEDIATE = "IMMEDIATE"
    AFTER_GRACE_PERIOD = "AFTER_GRACE_PERIOD"


class SuspensionActionTaken(enum.StrEnum):
    """What concrete action was taken when a rule fired."""

    SUSPENDED = "SUSPENDED"
    WARNING_SENT = "WARNING_SENT"
    NO_ACTION = "NO_ACTION"


class RuleScopeType(enum.StrEnum):
    GLOBAL = "GLOBAL"
    ORG = "ORG"


class SuspensionRuleType(enum.StrEnum):
    CREDIT_LIMIT = "CREDIT_LIMIT"
    BANK_TRANSFER = "BANK_TRANSFER"
    CREDIT_CARD = "CREDIT_CARD"
    CASH = "CASH"


class SuspensionConnector(enum.StrEnum):
    NONE = "NONE"
    AND = "AND"
    OR = "OR"


class SuspensionConditionType(enum.StrEnum):
    # Credit limit based
    INVOICE_OVERDUE_DAYS = "INVOICE_OVERDUE_DAYS"
    TOTAL_OVERDUE_AMOUNT = "TOTAL_OVERDUE_AMOUNT"
    CREDIT_UTILIZATION = "CREDIT_UTILIZATION"
    CREDIT_NOT_CLEARED_AFTER_DUE_DATE = "CREDIT_NOT_CLEARED_AFTER_DUE_DATE"
    # Bank transfer based
    TOTAL_OUTSTANDING_AMOUNT = "TOTAL_OUTSTANDING_AMOUNT"
    NUMBER_OF_UNPAID_INVOICES = "NUMBER_OF_UNPAID_INVOICES"
    # Credit card based
    PAYMENT_FAILURE_COUNT = "PAYMENT_FAILURE_COUNT"
    CONSECUTIVE_PAYMENT_FAILURE = "CONSECUTIVE_PAYMENT_FAILURE"
    CHARGEBACK_TRIGGERED = "CHARGEBACK_TRIGGERED"
    PAYMENT_RETRY_FAILURE_COUNT = "PAYMENT_RETRY_FAILURE_COUNT"
    # Cash based
    OUTSTANDING_CASH_BALANCE = "OUTSTANDING_CASH_BALANCE"
    CASH_INVOICE_OVERDUE_DAYS = "CASH_INVOICE_OVERDUE_DAYS"
    MAX_UNPAID_ORDERS = "MAX_UNPAID_ORDERS"
