"""Created-at bounds for QuickBooks sync log list filters."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from app.modules.orders.enums import SummaryPeriodPreset
from app.modules.orders.utils import resolve_summary_window

MAX_QB_LOG_FILTER_DAYS = 366


def resolve_qb_log_created_at_bounds(
    *,
    period: SummaryPeriodPreset | None,
    date_from: date | None,
    date_to: date | None,
    today: date | None = None,
) -> tuple[datetime | None, datetime | None]:
    """Return UTC ``(created_from inclusive, created_to exclusive)`` for ``QbSyncLog.created_at``.

    When no period or custom range is provided, returns ``(None, None)`` (no date filter).
    """
    anchor = today or date.today()
    if period is None and date_from is None and date_to is None:
        return None, None

    if period is not None:
        window = resolve_summary_window(period=period, date_from=None, date_to=None, today=anchor)
        range_from, range_to = window.current_from, window.current_to
    else:
        if date_from is None or date_to is None:
            raise ValueError("Both date_from and date_to are required when period is omitted")
        range_from, range_to = date_from, date_to

    if range_from > range_to:
        raise ValueError("date_from cannot be later than date_to")
    if range_to > anchor:
        raise ValueError("date_to cannot be in the future")
    span_days = (range_to - range_from).days + 1
    if span_days > MAX_QB_LOG_FILTER_DAYS:
        raise ValueError(f"Date range cannot exceed {MAX_QB_LOG_FILTER_DAYS} days")

    created_from = datetime.combine(range_from, time.min, tzinfo=UTC)
    created_to_exclusive = datetime.combine(range_to + timedelta(days=1), time.min, tzinfo=UTC)
    return created_from, created_to_exclusive
