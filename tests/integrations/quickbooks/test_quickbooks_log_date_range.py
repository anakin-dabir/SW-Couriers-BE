"""Unit tests for QuickBooks sync log date filter resolution."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.integrations.quickbooks.log_date_range import resolve_qb_log_created_at_bounds
from app.integrations.quickbooks.schemas import QuickBooksFailuresListQuery
from app.modules.orders.enums import SummaryPeriodPreset


def test_resolve_no_date_filter_returns_none_bounds() -> None:
    assert resolve_qb_log_created_at_bounds(period=None, date_from=None, date_to=None) == (None, None)


def test_resolve_last_7_days_preset() -> None:
    anchor = date(2026, 2, 25)
    created_from, created_to_exclusive = resolve_qb_log_created_at_bounds(
        period=SummaryPeriodPreset.LAST_7_DAYS,
        date_from=None,
        date_to=None,
        today=anchor,
    )
    assert created_from == datetime(2026, 2, 19, 0, 0, tzinfo=UTC)
    assert created_to_exclusive == datetime(2026, 2, 26, 0, 0, tzinfo=UTC)


def test_resolve_custom_inclusive_date_range() -> None:
    created_from, created_to_exclusive = resolve_qb_log_created_at_bounds(
        period=None,
        date_from=date(2026, 2, 19),
        date_to=date(2026, 2, 25),
        today=date(2026, 2, 25),
    )
    assert created_from == datetime(2026, 2, 19, 0, 0, tzinfo=UTC)
    assert created_to_exclusive == datetime(2026, 2, 26, 0, 0, tzinfo=UTC)


def test_resolve_rejects_future_date_to() -> None:
    with pytest.raises(ValueError, match="future"):
        resolve_qb_log_created_at_bounds(
            period=None,
            date_from=date(2026, 2, 1),
            date_to=date(2099, 1, 1),
            today=date(2026, 2, 25),
        )


def test_failures_list_query_rejects_period_with_custom_dates() -> None:
    with pytest.raises(PydanticValidationError, match="not both"):
        QuickBooksFailuresListQuery(
            period=SummaryPeriodPreset.LAST_7_DAYS,
            date_from=date(2026, 2, 19),
            date_to=date(2026, 2, 25),
        )


def test_failures_list_query_rejects_partial_custom_range() -> None:
    with pytest.raises(PydanticValidationError, match="date_to"):
        QuickBooksFailuresListQuery(date_from=date(2026, 2, 19))
