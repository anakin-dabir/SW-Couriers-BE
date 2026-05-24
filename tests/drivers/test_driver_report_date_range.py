"""Unit tests for driver report date window resolution."""

from __future__ import annotations

from datetime import date

import pytest

from app.common.exceptions import ValidationError
from app.modules.drivers.service import DriverService


def test_resolve_report_date_range_last_month() -> None:
    today = date(2026, 5, 21)
    start, end = DriverService.resolve_report_date_range(
        period="last_month",
        start_date=None,
        end_date=None,
        today=today,
    )
    assert start == date(2026, 4, 1)
    assert end == date(2026, 4, 30)


def test_resolve_home_summary_windows_last_month() -> None:
    today = date(2026, 5, 21)
    start, end, prev_start, prev_end = DriverService.resolve_home_summary_windows(
        period="last_month",
        start_date=None,
        end_date=None,
        today=today,
    )
    assert start == date(2026, 4, 1)
    assert end == date(2026, 4, 30)
    assert prev_end == date(2026, 3, 31)
    assert prev_start == date(2026, 3, 2)


def test_resolve_report_date_range_requires_dates_without_period() -> None:
    with pytest.raises(ValidationError, match="start_date and end_date"):
        DriverService.resolve_report_date_range(period=None, start_date=None, end_date=None)
