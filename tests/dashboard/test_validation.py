"""Unit tests for dashboard input validation."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.common.exceptions import ValidationError
from app.modules.dashboard.validation import (
    normalize_search,
    resolve_as_of_date,
    validate_as_of_date,
    validate_delivery_stop_status_filters,
    validate_pagination,
)
from app.modules.orders.enums import DeliveryStopStatus


def test_normalize_search_strips_and_drops_blank() -> None:
    assert normalize_search("  SW-123  ") == "SW-123"
    assert normalize_search("   ") is None
    assert normalize_search(None) is None


def test_validate_delivery_stop_status_filters_rejects_unknown() -> None:
    with pytest.raises(ValidationError, match="Invalid delivery stop status"):
        validate_delivery_stop_status_filters(["NOT_A_REAL_STATUS"])


def test_validate_delivery_stop_status_filters_accepts_valid() -> None:
    result = validate_delivery_stop_status_filters([DeliveryStopStatus.OUT_FOR_DELIVERY.value])
    assert result == [DeliveryStopStatus.OUT_FOR_DELIVERY.value]


def test_validate_as_of_date_rejects_far_future() -> None:
    with pytest.raises(ValidationError, match="future"):
        validate_as_of_date(date.today() + timedelta(days=30))


def test_validate_as_of_date_rejects_far_past() -> None:
    with pytest.raises(ValidationError, match="past"):
        validate_as_of_date(date.today() - timedelta(days=400))


def test_validate_pagination_bounds() -> None:
    with pytest.raises(ValidationError):
        validate_pagination(0, 20)
    with pytest.raises(ValidationError):
        validate_pagination(1, 101)
    assert validate_pagination(1, 50) == (1, 50)


def test_resolve_as_of_date_defaults_to_today() -> None:
    assert resolve_as_of_date(None) == date.today()


def test_validate_as_of_date_accepts_boundary_windows() -> None:
    today = date.today()
    assert validate_as_of_date(today + timedelta(days=7)) == today + timedelta(days=7)
    assert validate_as_of_date(today - timedelta(days=366)) == today - timedelta(days=366)


def test_validate_delivery_stop_status_filters_empty_returns_none() -> None:
    assert validate_delivery_stop_status_filters(None) is None
    assert validate_delivery_stop_status_filters([]) is None
