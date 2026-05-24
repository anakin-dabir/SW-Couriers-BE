from __future__ import annotations

import calendar
from datetime import date


def add_calendar_months(from_date: date, months: int) -> date:
    if months == 0:
        return from_date
    total_month_index = from_date.year * 12 + (from_date.month - 1) + months
    year, month0 = divmod(total_month_index, 12)
    month = month0 + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(from_date.day, last_day)
    return date(year, month, day)
