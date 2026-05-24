"""Unit tests for dashboard response schema invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.dashboard.v1.schemas import DashboardCountKpi, DeliveredTodayKpi


def test_count_kpi_rejects_mismatched_change_abs() -> None:
    with pytest.raises(ValidationError, match="change_abs"):
        DashboardCountKpi(
            current=10,
            previous=4,
            change_abs=99,
            change_pct=None,
            comparison_label="yesterday",
        )


def test_delivered_kpi_accepts_valid_success_rate() -> None:
    kpi = DeliveredTodayKpi(
        current=8,
        previous=5,
        change_abs=3,
        change_pct=60.0,
        success_rate_pct=80.0,
        previous_success_rate_pct=50.0,
        comparison_label="yesterday",
    )
    assert kpi.change_abs == 3


def test_delivered_kpi_rejects_success_rate_above_100() -> None:
    with pytest.raises(ValidationError):
        DeliveredTodayKpi(
            current=1,
            previous=0,
            change_abs=1,
            success_rate_pct=101.0,
            comparison_label="yesterday",
        )
