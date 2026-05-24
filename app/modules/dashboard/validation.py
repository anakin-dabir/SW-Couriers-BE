"""Input normalization and validation for dashboard endpoints."""

from __future__ import annotations

from datetime import date, timedelta

from app.common.exceptions import ValidationError
from app.modules.orders.enums import DeliveryStopStatus

_MAX_AS_OF_AGE_DAYS = 366
_MAX_AS_OF_FUTURE_DAYS = 7


def resolve_as_of_date(as_of_date: date | None) -> date:
    """Use explicit reference day or UTC today."""
    return as_of_date or date.today()


def validate_as_of_date(as_of_date: date) -> date:
    """Reject reference dates far outside a sensible reporting window."""
    today = date.today()
    if as_of_date > today + timedelta(days=_MAX_AS_OF_FUTURE_DAYS):
        raise ValidationError(f"as_of_date cannot be more than {_MAX_AS_OF_FUTURE_DAYS} days in the future")
    if as_of_date < today - timedelta(days=_MAX_AS_OF_AGE_DAYS):
        raise ValidationError(f"as_of_date cannot be more than {_MAX_AS_OF_AGE_DAYS} days in the past")
    return as_of_date


def normalize_search(search: str | None) -> str | None:
    if search is None:
        return None
    trimmed = search.strip()
    return trimmed if trimmed else None


def validate_delivery_stop_status_filters(statuses: list[str] | None) -> list[str] | None:
    if not statuses:
        return None
    allowed = {s.value for s in DeliveryStopStatus}
    invalid = sorted({s for s in statuses if s not in allowed})
    if invalid:
        raise ValidationError(f"Invalid delivery stop status filter: {', '.join(invalid)}")
    return statuses


def validate_pagination(page: int, size: int) -> tuple[int, int]:
    if page < 1:
        raise ValidationError("page must be at least 1")
    if size < 1 or size > 100:
        raise ValidationError("size must be between 1 and 100")
    return page, size
