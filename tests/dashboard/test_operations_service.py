"""Unit tests for operations dashboard KPI builders."""

from __future__ import annotations

import pytest

from app.modules.dashboard.operations_service import _count_kpi, _delivered_kpi


def test_count_kpi_change_abs_and_pct() -> None:
    kpi = _count_kpi(42, 37, comparison_label="yesterday")
    assert kpi.current == 42
    assert kpi.previous == 37
    assert kpi.change_abs == 5
    assert kpi.change_pct == pytest.approx(13.51, rel=0.01)


def test_count_kpi_change_pct_none_when_previous_zero() -> None:
    kpi = _count_kpi(10, 0, comparison_label="yesterday")
    assert kpi.change_abs == 10
    assert kpi.change_pct is None


def test_delivered_kpi_success_rate() -> None:
    kpi = _delivered_kpi(95, 90, 5, 10)
    assert kpi.success_rate_pct == 95.0
    assert kpi.previous_success_rate_pct == pytest.approx(90.0, rel=0.01)
    assert kpi.change_abs == 5


def test_delivered_kpi_success_rate_none_when_no_attempts() -> None:
    kpi = _delivered_kpi(0, 0, 0, 0)
    assert kpi.success_rate_pct is None
    assert kpi.previous_success_rate_pct is None
