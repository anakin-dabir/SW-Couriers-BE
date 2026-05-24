"""Small internal result types for the notifications module."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SystemDefaultRow:
    """Lightweight read-only view of a ``system_notification_defaults`` row.

    Used by cached read paths so the repository can return a value that
    both direct DB reads and Redis-cached reads populate identically. The
    service layer only needs these columns to drive the cascade.
    """

    id: str
    notification_type: str
    event: str
    email_enabled: bool
    sms_enabled: bool
    email_template_id: str | None
    sms_template_id: str | None
