"""Unit tests for driver report hardening helpers."""

from __future__ import annotations

import math

from app.modules.drivers.service import DriverService


class _Event:
    def __init__(self, metadata: object) -> None:
        self.event_metadata = metadata


def test_safe_float_rejects_non_finite() -> None:
    assert DriverService._safe_float(float("inf")) is None
    assert DriverService._safe_float(float("nan")) is None
    assert DriverService._safe_float("not-a-number") is None
    assert DriverService._safe_float(None) is None
    assert DriverService._safe_float(42.5) == 42.5


def test_event_metadata_dict_rejects_non_dict() -> None:
    assert DriverService._event_metadata_dict(_Event("string")) == {}
    assert DriverService._event_metadata_dict(_Event(None)) == {}
    assert DriverService._event_metadata_dict(_Event({"speed_mph": math.inf})) == {"speed_mph": math.inf}
