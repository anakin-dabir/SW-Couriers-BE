"""Validation helpers for account statements."""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.common.exceptions import ValidationError
from app.modules.account_statements.constants import MAX_PERIOD_DAYS

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_period(period_start: date, period_end: date, *, today: date | None = None) -> None:
    if period_start > period_end:
        raise ValidationError("period_start must be on or before period_end")
    if today is not None and period_end > today:
        raise ValidationError("period_end cannot be in the future")
    span = (period_end - period_start).days + 1
    if span > MAX_PERIOD_DAYS:
        raise ValidationError(f"Statement period cannot exceed {MAX_PERIOD_DAYS} days")


def validate_email(email: str) -> str:
    cleaned = (email or "").strip().lower()
    if not cleaned or not _EMAIL_RE.match(cleaned):
        raise ValidationError("recipient_email must be a valid email address")
    return cleaned
