"""Unit tests for account statement schedule timing."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.common.exceptions import ValidationError
from app.modules.account_statements.enums import StatementScheduleFrequency
from app.modules.account_statements.constants import CUSTOM_SCHEDULE_ONCE, DEFAULT_SCHEDULE_VALID_TO
from app.modules.account_statements.scheduling import (
    initial_next_run_at_utc,
    is_once_custom_schedule,
    next_run_at_utc,
    normalize_frequency,
    parse_interval_days,
    resolve_schedule_window,
    resolve_timezone,
    statement_period_for_custom_run,
    statement_period_for_run,
    validate_schedule_inputs,
)


def test_normalize_frequency_rejects_unknown() -> None:
    with pytest.raises(ValidationError, match="frequency must be one of"):
        normalize_frequency("WEEKLY")


def test_monthly_first_period_on_april_first() -> None:
    start, end = statement_period_for_run(
        StatementScheduleFrequency.MONTHLY_FIRST,
        run_local_date=date(2026, 4, 1),
    )
    assert start == date(2026, 3, 1)
    assert end == date(2026, 3, 31)


def test_quarterly_period_on_july_first() -> None:
    start, end = statement_period_for_run(
        StatementScheduleFrequency.QUARTERLY,
        run_local_date=date(2026, 7, 1),
    )
    assert start == date(2026, 4, 1)
    assert end == date(2026, 6, 30)


def test_next_monthly_run_after_mid_month() -> None:
    tz = ZoneInfo("Europe/London")
    ref = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    nxt = next_run_at_utc(
        frequency=StatementScheduleFrequency.MONTHLY_FIRST,
        tz=tz,
        valid_from=date(2026, 1, 1),
        valid_to=date(2027, 12, 31),
        interval_storage=None,
        after_utc=ref,
    )
    assert nxt is not None
    assert nxt.astimezone(tz).date() == date(2026, 6, 1)


def test_custom_once_mode_when_interval_omitted() -> None:
    freq, tz, stored, resolved_from, resolved_to = validate_schedule_inputs(
        frequency="CUSTOM",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 3, 31),
        timezone="Europe/London",
        interval_days=None,
    )
    assert freq == StatementScheduleFrequency.CUSTOM
    assert stored == "once"
    assert resolved_from == date(2026, 1, 1)
    assert resolved_to == date(2026, 3, 31)


def test_custom_once_period_covers_full_window() -> None:
    start, end = statement_period_for_custom_run(
        run_local_date=date(2026, 3, 31),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 3, 31),
        last_run_at=None,
        tz=ZoneInfo("Europe/London"),
        interval_storage="once",
    )
    assert start == date(2026, 1, 1)
    assert end == date(2026, 3, 31)


def test_custom_once_next_run_on_valid_to() -> None:
    ref = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    tz = ZoneInfo("Europe/London")
    nxt = initial_next_run_at_utc(
        frequency=StatementScheduleFrequency.CUSTOM,
        tz=tz,
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 3, 31),
        interval_storage="once",
        now_utc=ref,
    )
    assert nxt is not None
    assert nxt.astimezone(tz).date() == date(2026, 3, 31)


def test_custom_recurring_still_requires_valid_interval() -> None:
    with pytest.raises(ValidationError, match="interval_days must be between"):
        validate_schedule_inputs(
            frequency="CUSTOM",
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            timezone="Europe/London",
            interval_days=3,
        )


def test_custom_requires_valid_from_and_valid_to() -> None:
    tz = ZoneInfo("Europe/London")
    with pytest.raises(ValidationError, match="valid_from and valid_to are required"):
        resolve_schedule_window(
            frequency=StatementScheduleFrequency.CUSTOM,
            valid_from=None,
            valid_to=date(2026, 12, 31),
            tz=tz,
        )


def test_monthly_defaults_open_ended_window() -> None:
    tz = ZoneInfo("Europe/London")
    ref = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    start, end = resolve_schedule_window(
        frequency=StatementScheduleFrequency.MONTHLY_FIRST,
        valid_from=None,
        valid_to=None,
        tz=tz,
        now_utc=ref,
    )
    assert start == date(2026, 5, 21)
    assert end == DEFAULT_SCHEDULE_VALID_TO


def test_custom_interval_next_run() -> None:
    freq, tz, stored, resolved_from, resolved_to = validate_schedule_inputs(
        frequency="CUSTOM",
        valid_from=date(2026, 1, 1),
        valid_to=date(2027, 12, 31),
        timezone="Europe/London",
        interval_days=30,
    )
    assert stored == "30"
    assert resolved_from == date(2026, 1, 1)
    assert resolved_to == date(2027, 12, 31)
    ref = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    nxt = initial_next_run_at_utc(
        frequency=freq,
        tz=tz,
        valid_from=resolved_from,
        valid_to=resolved_to,
        interval_storage=stored,
        now_utc=ref,
    )
    assert nxt is not None
    assert nxt.astimezone(tz).date() == date(2026, 1, 31)


def test_resolve_timezone_rejects_unknown() -> None:
    with pytest.raises(ValidationError, match="Unknown timezone"):
        resolve_timezone("Not/A_Timezone")


def test_custom_once_overdue_runs_immediately() -> None:
    tz = ZoneInfo("Europe/London")
    ref = datetime(2026, 3, 31, 12, 0, tzinfo=UTC)
    nxt = next_run_at_utc(
        frequency=StatementScheduleFrequency.CUSTOM,
        tz=tz,
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 3, 31),
        interval_storage=CUSTOM_SCHEDULE_ONCE,
        after_utc=ref,
    )
    assert nxt is not None
    assert nxt <= ref + timedelta(seconds=2)


def test_custom_once_after_valid_to_returns_none() -> None:
    tz = ZoneInfo("Europe/London")
    ref = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    nxt = next_run_at_utc(
        frequency=StatementScheduleFrequency.CUSTOM,
        tz=tz,
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 3, 31),
        interval_storage=CUSTOM_SCHEDULE_ONCE,
        after_utc=ref,
    )
    assert nxt is None


def test_parse_interval_days_rejects_once_marker() -> None:
    with pytest.raises(ValidationError, match="one-time CUSTOM"):
        parse_interval_days(CUSTOM_SCHEDULE_ONCE)


def test_is_once_custom_schedule_helper() -> None:
    assert is_once_custom_schedule("once") is True
    assert is_once_custom_schedule("30") is False
