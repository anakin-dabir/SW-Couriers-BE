"""Unit tests for driver schedule holiday rules."""

from __future__ import annotations

from types import SimpleNamespace

from app.modules.drivers.schedule.holiday_rules import (
    holiday_blocks_driver,
    is_driver_allowed_on_holiday,
)


def _holiday(*, allow_shifts: bool, driver_ids: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        allow_shifts=allow_shifts,
        allowed_drivers=[SimpleNamespace(driver_id=did) for did in driver_ids],
    )


class TestHolidayRules:
    def test_blocks_when_allow_shifts_false(self) -> None:
        h = _holiday(allow_shifts=False, driver_ids=["d1"])
        assert holiday_blocks_driver("d1", h) is True
        assert is_driver_allowed_on_holiday("d1", h) is False

    def test_blocks_when_allow_shifts_true_but_empty_list(self) -> None:
        h = _holiday(allow_shifts=True, driver_ids=[])
        assert holiday_blocks_driver("d1", h) is True

    def test_allows_listed_driver_only(self) -> None:
        h = _holiday(allow_shifts=True, driver_ids=["d1"])
        assert is_driver_allowed_on_holiday("d1", h) is True
        assert holiday_blocks_driver("d1", h) is False
        assert holiday_blocks_driver("d2", h) is True
