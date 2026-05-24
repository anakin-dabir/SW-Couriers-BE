from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta


def ends_at_after_duration(
    start: datetime,
    *,
    months: int,
    days: int,
    hours: int,
) -> datetime:
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    y = start.year
    mth = start.month + months
    y += (mth - 1) // 12
    mth = (mth - 1) % 12 + 1
    day = min(start.day, calendar.monthrange(y, mth)[1])
    partial = start.replace(year=y, month=mth, day=day)
    return partial + timedelta(days=days, hours=hours)


def humanize_cooldown_remaining(total_seconds: int) -> str:
    if total_seconds <= 0:
        return "0 seconds remaining"
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes and days == 0:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if not parts:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return ", ".join(parts) + " remaining"
