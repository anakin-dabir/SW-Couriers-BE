"""Recurring account statement schedule timing (period windows and next run).

Uses stdlib ``zoneinfo`` + calendar date math only. Recurring execution is driven by the
existing Arq cron job ``run_account_statement_schedules_task`` on the LOW worker.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.common.exceptions import ValidationError
from app.modules.account_statements.constants import (
    CUSTOM_SCHEDULE_ONCE,
    DEFAULT_SCHEDULE_VALID_TO,
    MAX_CUSTOM_INTERVAL_DAYS,
    MIN_CUSTOM_INTERVAL_DAYS,
    SCHEDULE_RUN_HOUR,
    SCHEDULE_RUN_MINUTE,
)
from app.modules.account_statements.enums import StatementScheduleFrequency


def resolve_timezone(name: str) -> ZoneInfo:
    candidate = (name or "Europe/London").strip() or "Europe/London"
    try:
        return ZoneInfo(candidate)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError(f"Unknown timezone: {candidate}") from exc


def normalize_frequency(frequency: str) -> StatementScheduleFrequency:
    raw = (frequency or "").strip().upper()
    try:
        return StatementScheduleFrequency(raw)
    except ValueError as exc:
        allowed = ", ".join(s.value for s in StatementScheduleFrequency)
        raise ValidationError(f"frequency must be one of: {allowed}") from exc


def is_once_custom_schedule(stored: str | None) -> bool:
    return (stored or "").strip().lower() == CUSTOM_SCHEDULE_ONCE


def parse_interval_days(stored: str | None) -> int:
    """Read CUSTOM interval from ``custom_cron`` column (stores plain day count as string)."""
    raw = (stored or "").strip()
    if not raw:
        raise ValidationError("interval_days is required when frequency is CUSTOM")
    if is_once_custom_schedule(raw):
        raise ValidationError("interval_days is not used for one-time CUSTOM schedules")
    try:
        days = int(raw)
    except ValueError as exc:
        raise ValidationError("interval_days must be a whole number of days") from exc
    if days < MIN_CUSTOM_INTERVAL_DAYS or days > MAX_CUSTOM_INTERVAL_DAYS:
        raise ValidationError(
            f"interval_days must be between {MIN_CUSTOM_INTERVAL_DAYS} and {MAX_CUSTOM_INTERVAL_DAYS}"
        )
    return days


def format_interval_storage(interval_days: int) -> str:
    return str(interval_days)


def resolve_schedule_window(
    *,
    frequency: StatementScheduleFrequency,
    valid_from: date | None,
    valid_to: date | None,
    tz: ZoneInfo,
    now_utc: datetime | None = None,
) -> tuple[date, date]:
    """Resolve schedule active window.

    CUSTOM schedules require explicit ``valid_from`` and ``valid_to`` (statement cadence window).
    MONTHLY_FIRST and QUARTERLY default to ongoing: start today in org timezone, open-ended end.
    """
    today_local = local_date_now(tz=tz, now_utc=now_utc)
    if frequency == StatementScheduleFrequency.CUSTOM:
        if valid_from is None or valid_to is None:
            raise ValidationError("valid_from and valid_to are required when frequency is CUSTOM")
        resolved_from = valid_from
        resolved_to = valid_to
    else:
        resolved_from = valid_from or today_local
        resolved_to = valid_to or DEFAULT_SCHEDULE_VALID_TO
    if resolved_from > resolved_to:
        raise ValidationError("valid_from must be on or before valid_to")
    return resolved_from, resolved_to


def validate_schedule_inputs(
    *,
    frequency: str,
    valid_from: date | None,
    valid_to: date | None,
    timezone: str,
    interval_days: int | None,
    now_utc: datetime | None = None,
) -> tuple[StatementScheduleFrequency, ZoneInfo, str | None, date, date]:
    freq = normalize_frequency(frequency)
    tz = resolve_timezone(timezone)
    resolved_from, resolved_to = resolve_schedule_window(
        frequency=freq,
        valid_from=valid_from,
        valid_to=valid_to,
        tz=tz,
        now_utc=now_utc,
    )
    stored_interval: str | None = None
    if freq == StatementScheduleFrequency.CUSTOM:
        if interval_days is None:
            stored_interval = CUSTOM_SCHEDULE_ONCE
        else:
            stored_interval = format_interval_storage(interval_days)
            parse_interval_days(stored_interval)
    elif interval_days is not None:
        raise ValidationError("interval_days is only allowed when frequency is CUSTOM")
    return freq, tz, stored_interval, resolved_from, resolved_to


def local_date_now(*, tz: ZoneInfo, now_utc: datetime | None = None) -> date:
    ref = now_utc or datetime.now(UTC)
    return ref.astimezone(tz).date()


def _local_run_dt(day: date, *, tz: ZoneInfo) -> datetime:
    local = datetime.combine(day, time(SCHEDULE_RUN_HOUR, SCHEDULE_RUN_MINUTE), tzinfo=tz)
    return local.astimezone(UTC)


def _first_of_month(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _quarter_start(d: date) -> date:
    quarter_month = ((d.month - 1) // 3) * 3 + 1
    return date(d.year, quarter_month, 1)


def _next_quarter_start(d: date) -> date:
    q_start = _quarter_start(d)
    if d == q_start:
        return _add_months(q_start, 3)
    return q_start


def _is_monthly_run_day(local_day: date) -> bool:
    return local_day.day == 1


def _is_quarterly_run_day(local_day: date) -> bool:
    return local_day.day == 1 and local_day.month in {1, 4, 7, 10}


def statement_period_for_run(
    frequency: StatementScheduleFrequency,
    *,
    run_local_date: date,
) -> tuple[date, date]:
    """Inclusive statement period produced when the schedule fires on ``run_local_date``."""
    if frequency == StatementScheduleFrequency.MONTHLY_FIRST:
        if not _is_monthly_run_day(run_local_date):
            raise ValidationError("MONTHLY_FIRST schedules only run on the 1st of the month")
        last_prev = _first_of_month(run_local_date) - timedelta(days=1)
        return _first_of_month(last_prev), last_prev
    if frequency == StatementScheduleFrequency.QUARTERLY:
        if not _is_quarterly_run_day(run_local_date):
            raise ValidationError("QUARTERLY schedules only run on the 1st of Jan, Apr, Jul, or Oct")
        this_q = _quarter_start(run_local_date)
        end = this_q - timedelta(days=1)
        return _quarter_start(end), end
    raise ValidationError("CUSTOM schedule periods use statement_period_for_custom_run")


def statement_period_for_custom_run(
    *,
    run_local_date: date,
    valid_from: date,
    valid_to: date,
    last_run_at: datetime | None,
    tz: ZoneInfo,
    interval_storage: str | None = None,
) -> tuple[date, date]:
    if is_once_custom_schedule(interval_storage):
        return valid_from, valid_to
    if last_run_at is None:
        period_start = valid_from
    else:
        period_start = last_run_at.astimezone(tz).date() + timedelta(days=1)
    period_end = run_local_date - timedelta(days=1)
    if period_start > period_end:
        raise ValidationError("No statement period to generate for this CUSTOM schedule run")
    return period_start, period_end


def _next_preset_local_run_day(
    frequency: StatementScheduleFrequency,
    *,
    after_local: date,
) -> date:
    if frequency == StatementScheduleFrequency.MONTHLY_FIRST:
        candidate = _first_of_month(after_local)
        if candidate < after_local:
            candidate = _add_months(_first_of_month(after_local), 1)
        return candidate
    if frequency == StatementScheduleFrequency.QUARTERLY:
        candidate = _next_quarter_start(after_local)
        if candidate < after_local:
            candidate = _add_months(candidate, 3)
        return candidate
    raise ValidationError("Unsupported preset frequency")


def _next_interval_run_utc(
    *,
    interval_days: int,
    tz: ZoneInfo,
    valid_from: date,
    valid_to: date,
    after_utc: datetime,
) -> datetime | None:
    anchor = valid_from
    while anchor <= valid_to:
        run_utc = _local_run_dt(anchor, tz=tz)
        if run_utc > after_utc:
            return run_utc
        anchor += timedelta(days=interval_days)
    return None


def _next_once_run_utc(
    *,
    valid_to: date,
    tz: ZoneInfo,
    after_utc: datetime,
) -> datetime | None:
    """Single CUSTOM run on ``valid_to`` at org local schedule time (or immediately if overdue)."""
    if after_utc.astimezone(tz).date() > valid_to:
        return None
    run_utc = _local_run_dt(valid_to, tz=tz)
    if run_utc > after_utc:
        return run_utc
    return after_utc + timedelta(seconds=1)


def next_run_at_utc(
    *,
    frequency: StatementScheduleFrequency,
    tz: ZoneInfo,
    valid_from: date,
    valid_to: date,
    interval_storage: str | None,
    after_utc: datetime | None = None,
) -> datetime | None:
    """Next UTC fire time strictly after ``after_utc``, within ``valid_from``..``valid_to``."""
    ref_utc = after_utc or datetime.now(UTC)
    if ref_utc.astimezone(tz).date() > valid_to:
        return None

    if frequency == StatementScheduleFrequency.CUSTOM:
        if is_once_custom_schedule(interval_storage):
            return _next_once_run_utc(valid_to=valid_to, tz=tz, after_utc=ref_utc)
        return _next_interval_run_utc(
            interval_days=parse_interval_days(interval_storage),
            tz=tz,
            valid_from=valid_from,
            valid_to=valid_to,
            after_utc=ref_utc,
        )

    start_local = max(valid_from, ref_utc.astimezone(tz).date())
    run_day = _next_preset_local_run_day(frequency, after_local=start_local)
    while run_day <= valid_to:
        run_utc = _local_run_dt(run_day, tz=tz)
        if run_utc > ref_utc:
            return run_utc
        run_day = _next_preset_local_run_day(
            frequency,
            after_local=run_day + timedelta(days=1),
        )
    return None


def initial_next_run_at_utc(
    *,
    frequency: StatementScheduleFrequency,
    tz: ZoneInfo,
    valid_from: date,
    valid_to: date,
    interval_storage: str | None,
    now_utc: datetime | None = None,
) -> datetime | None:
    """First scheduled run at or after ``valid_from``."""
    ref = now_utc or datetime.now(UTC)
    return next_run_at_utc(
        frequency=frequency,
        tz=tz,
        valid_from=valid_from,
        valid_to=valid_to,
        interval_storage=interval_storage,
        after_utc=ref - timedelta(seconds=1),
    )
