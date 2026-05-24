"""Default UK holiday templates used for initial year seeding."""

from __future__ import annotations

from datetime import date, timedelta

from app.modules.holidays.enums import HolidayAudience


def _first_weekday_of_month(year: int, month: int, weekday: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    current = date(year + 1, 1, 1) - timedelta(days=1) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def _observed_new_year_day(year: int) -> date:
    d = date(year, 1, 1)
    if d.weekday() == 5:  # Saturday
        return date(year, 1, 3)
    if d.weekday() == 6:  # Sunday
        return date(year, 1, 2)
    return d


def _observed_christmas_and_boxing_days(year: int) -> tuple[date, date]:
    christmas = date(year, 12, 25)
    boxing = date(year, 12, 26)

    if christmas.weekday() == 5:  # Sat
        return date(year, 12, 27), date(year, 12, 28)
    if christmas.weekday() == 6:  # Sun
        return date(year, 12, 27), date(year, 12, 26)
    if boxing.weekday() == 5:  # Sat
        return christmas, date(year, 12, 28)
    if boxing.weekday() == 6:  # Sun
        return christmas, date(year, 12, 27)
    return christmas, boxing


def _easter_sunday(year: int) -> date:
    """Gregorian Easter date (Anonymous Gregorian algorithm)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l_value = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_value) // 451
    month = (h + l_value - 7 * m + 114) // 31
    day = ((h + l_value - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def build_universal_uk_holidays(year: int) -> list[dict[str, object]]:
    """Build common UK-wide bank holidays for the provided year."""
    easter = _easter_sunday(year)
    good_friday = easter - timedelta(days=2)
    easter_monday = easter + timedelta(days=1)
    early_may = _first_weekday_of_month(year, 5, 0)
    spring_bank = _last_weekday_of_month(year, 5, 0)
    summer_bank = _last_weekday_of_month(year, 8, 0)
    new_year = _observed_new_year_day(year)
    christmas_day, boxing_day = _observed_christmas_and_boxing_days(year)

    all_day = {
        "year": year,
        "audience": HolidayAudience.BOTH.value,
        "allow_shifts": False,
    }
    return [
        {"name": "New Year's Day", "start_date": new_year, "end_date": new_year, **all_day},
        {"name": "Good Friday", "start_date": good_friday, "end_date": good_friday, **all_day},
        {"name": "Easter Monday", "start_date": easter_monday, "end_date": easter_monday, **all_day},
        {"name": "Early May Bank Holiday", "start_date": early_may, "end_date": early_may, **all_day},
        {"name": "Spring Bank Holiday", "start_date": spring_bank, "end_date": spring_bank, **all_day},
        {"name": "Summer Bank Holiday", "start_date": summer_bank, "end_date": summer_bank, **all_day},
        {"name": "Christmas Day", "start_date": christmas_day, "end_date": christmas_day, **all_day},
        {"name": "Boxing Day", "start_date": boxing_day, "end_date": boxing_day, **all_day},
    ]
