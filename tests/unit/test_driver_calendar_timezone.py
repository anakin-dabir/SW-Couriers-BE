"""Fast unit tests for depot-local calendar helpers (no DB, no ASGI).

Expectations are derived from ``zoneinfo.ZoneInfo`` so behaviour matches the installed IANA database
(works whether ``tzdata`` supplies zones or the OS does).
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

try:
    import tzdata  # noqa: F401
except ImportError:
    pass

from app.modules.drivers.service import DriverService


@pytest.mark.unit
def test_calendar_date_in_zone_matches_zoneinfo_for_london() -> None:
    utc = datetime(2026, 6, 15, 23, 30, 0, tzinfo=UTC)
    expected = utc.astimezone(ZoneInfo("Europe/London")).date()
    assert DriverService._calendar_date_in_zone(utc_now=utc, tz_name="Europe/London") == expected


@pytest.mark.unit
def test_calendar_date_in_zone_unknown_zone_falls_back_to_same_as_default_london() -> None:
    utc = datetime(2026, 6, 15, 23, 30, 0, tzinfo=UTC)
    london_day = DriverService._calendar_date_in_zone(utc_now=utc, tz_name="Europe/London")
    fallback_day = DriverService._calendar_date_in_zone(utc_now=utc, tz_name="Invalid/Nowhere")
    assert fallback_day == london_day
