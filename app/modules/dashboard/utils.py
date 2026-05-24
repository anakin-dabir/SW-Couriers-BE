"""Shared helpers for dashboard KPI math."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


@dataclass(frozen=True, slots=True)
class DayWindow:
    """Inclusive calendar-day bounds in UTC for event filtering."""

    start: datetime
    end_exclusive: datetime


def utc_day_window(day: date) -> DayWindow:
    start = datetime.combine(day, time.min)
    end_exclusive = datetime.combine(day + timedelta(days=1), time.min)
    return DayWindow(start=start, end_exclusive=end_exclusive)


def pct_change(current: float, previous: float) -> float | None:
    if previous <= 0:
        return None
    return round(((current - previous) / previous) * 100.0, 2)


def success_rate_pct(delivered: int, failed: int) -> float | None:
    attempts = delivered + failed
    if attempts <= 0:
        return None
    return round((delivered / attempts) * 100.0, 1)
