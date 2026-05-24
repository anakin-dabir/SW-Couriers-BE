"""Unit tests for driver operational configuration schemas."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.drivers.v1.schemas import DriverOperationalConfigurationUpdateRequest


@pytest.mark.unit
def test_operational_configuration_update_request_roundtrip() -> None:
    body = DriverOperationalConfigurationUpdateRequest(
        okay_with_layover=False,
        layover_cost_per_night=Decimal("0"),
        max_layover_nights=0,
        expected_version=12,
    )
    dumped = body.model_dump(mode="json")
    assert dumped["layover_cost_per_night"] == "0.00"
    assert dumped["okay_with_layover"] is False
    assert dumped["expected_version"] == 12
