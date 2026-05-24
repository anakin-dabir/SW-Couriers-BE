"""Unit tests for dashboard KPI helpers."""

from __future__ import annotations

from datetime import date

from app.modules.dashboard.utils import pct_change, success_rate_pct, utc_day_window


def test_pct_change_returns_none_when_previous_zero() -> None:
    assert pct_change(10.0, 0.0) is None


def test_pct_change_rounds_to_two_decimals() -> None:
    assert pct_change(1284.0, 1209.0) == 6.2


def test_success_rate_pct() -> None:
    assert success_rate_pct(95, 5) == 95.0
    assert success_rate_pct(0, 0) is None


def test_utc_day_window_bounds() -> None:
    window = utc_day_window(date(2026, 5, 19))
    assert window.start.isoformat().startswith("2026-05-19")
    assert window.end_exclusive.isoformat().startswith("2026-05-20")
