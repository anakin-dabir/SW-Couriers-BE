"""Account statement enums."""

import enum


class StatementPdfStatus(enum.StrEnum):
    PENDING = "PENDING"
    GENERATING = "GENERATING"
    READY = "READY"
    FAILED = "FAILED"


class StatementCreatedByType(enum.StrEnum):
    SYSTEM = "SYSTEM"
    ADMIN = "ADMIN"
    CLIENT = "CLIENT"


class StatementRowType(enum.StrEnum):
    INVOICE = "INVOICE"
    PAYMENT = "PAYMENT"
    CREDIT_NOTE = "CREDIT_NOTE"
    REFUND = "REFUND"
    OPENING_BALANCE = "OPENING_BALANCE"


class StatementScheduleFrequency(enum.StrEnum):
    MONTHLY_FIRST = "MONTHLY_FIRST"
    QUARTERLY = "QUARTERLY"
    CUSTOM = "CUSTOM"


class StatementScheduleStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"


class StatementDeliveryStatus(enum.StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
