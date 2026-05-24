"""Invoice enums.

- InvoiceStatus: document lifecycle only (DRAFT -> SENT). Do not use for payment outcome.
- PaymentStatus: paid/overdue/void/written_off; computed from amounts and due_date, or set by void/write-off.
- InvoiceEventType: append-only activity log (CREATED, FINALIZED, VOIDED, etc.).
- PdfArtifactStatus: PDF job state (GENERATING, READY, FAILED).
- CreditNoteStatus: credit memo state (ISSUED, VOIDED, WRITTEN_OFF).
"""

import enum


class InvoiceStatus(enum.StrEnum):
    """Invoice lifecycle status (document state). Only DRAFT and SENT. Use PaymentStatus for outcome (void, written off, paid, etc.)."""

    DRAFT = "DRAFT"
    SENT = "SENT"


class PaymentStatus(enum.StrEnum):
    """Payment/outcome status derived from billing allocations, due_date, and outcome events.

    REFUNDED and DISPUTED are list/summary filter tokens only (not persisted on ``invoices.payment_status``).
    """

    UNPAID = "UNPAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    VOID = "VOID"
    WRITTEN_OFF = "WRITTEN_OFF"
    # Portal filters only — not stored on invoices.payment_status; resolved via refunds / payment metadata.
    REFUNDED = "REFUNDED"
    DISPUTED = "DISPUTED"


class InvoiceEventType(enum.StrEnum):
    """Append-only invoice activity events."""

    CREATED = "CREATED"
    DRAFT_SAVED = "DRAFT_SAVED"
    FINALIZED = "FINALIZED"
    VOIDED = "VOIDED"
    WRITTEN_OFF = "WRITTEN_OFF"
    CREDIT_APPLIED = "CREDIT_APPLIED"


class PdfArtifactStatus(enum.StrEnum):
    """Status of a generated invoice PDF artifact."""

    GENERATING = "GENERATING"
    READY = "READY"
    FAILED = "FAILED"


class CreditNoteStatus(enum.StrEnum):
    """Credit note (credit memo) status."""

    ISSUED = "ISSUED"
    VOIDED = "VOIDED"
    WRITTEN_OFF = "WRITTEN_OFF"
