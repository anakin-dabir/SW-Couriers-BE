from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.modules.orders.enums import SummaryPeriodPreset


@dataclass(frozen=True, slots=True)
class SummaryWindow:
    current_from: date
    current_to: date
    previous_from: date
    previous_to: date
    comparison_label: str


def _last_day_of_month(d: date) -> date:
    nxt = d.replace(day=28) + timedelta(days=4)
    return nxt - timedelta(days=nxt.day)


def _previous_full_calendar_month(anchor: date) -> tuple[date, date]:
    first_of_this = anchor.replace(day=1)
    last_of_prev = first_of_this - timedelta(days=1)
    first_of_prev = last_of_prev.replace(day=1)
    return first_of_prev, last_of_prev


def _rolling_previous_equal_span(current_from: date, current_to: date) -> tuple[date, date]:
    span_days = (current_to - current_from).days + 1
    prev_to = current_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=span_days - 1)
    return prev_from, prev_to


def _monday_sunday(d: date) -> tuple[date, date]:
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def resolve_summary_window(
    *,
    period: SummaryPeriodPreset | None,
    date_from: date | None,
    date_to: date | None,
    today: date,
) -> SummaryWindow:
    if period is not None:
        if period == SummaryPeriodPreset.TODAY:
            c_from, c_to = today, today
            p_from, p_to = _rolling_previous_equal_span(c_from, c_to)
            return SummaryWindow(
                c_from,
                c_to,
                p_from,
                p_to,
                comparison_label="yesterday",
            )
        if period == SummaryPeriodPreset.YESTERDAY:
            yesterday = today - timedelta(days=1)
            c_from, c_to = yesterday, yesterday
            p_from, p_to = _rolling_previous_equal_span(c_from, c_to)
            return SummaryWindow(
                c_from,
                c_to,
                p_from,
                p_to,
                comparison_label="day before",
            )
        if period == SummaryPeriodPreset.LAST_7_DAYS:
            c_from, c_to = today - timedelta(days=6), today
            p_from, p_to = _rolling_previous_equal_span(c_from, c_to)
            return SummaryWindow(
                c_from,
                c_to,
                p_from,
                p_to,
                comparison_label="previous 7 days",
            )
        if period == SummaryPeriodPreset.LAST_30_DAYS:
            c_from, c_to = today - timedelta(days=29), today
            p_from, p_to = _rolling_previous_equal_span(c_from, c_to)
            return SummaryWindow(
                c_from,
                c_to,
                p_from,
                p_to,
                comparison_label="previous 30 days",
            )
        if period == SummaryPeriodPreset.LAST_WEEK:
            this_monday, _ = _monday_sunday(today)
            c_from = this_monday - timedelta(days=7)
            c_to = c_from + timedelta(days=6)
            p_from, p_to = _rolling_previous_equal_span(c_from, c_to)
            return SummaryWindow(
                c_from,
                c_to,
                p_from,
                p_to,
                comparison_label="previous week",
            )
        if period == SummaryPeriodPreset.LAST_MONTH:
            c_from, c_to = _previous_full_calendar_month(today)
            p_from, p_to = _previous_full_calendar_month(c_from)
            return SummaryWindow(
                c_from,
                c_to,
                p_from,
                p_to,
                comparison_label="previous month",
            )
        raise ValueError("Unsupported summary period")

    if date_from is None or date_to is None:
        raise ValueError("Either `period` or both `date_from` and `date_to` is required")
    c_from, c_to = date_from, date_to
    p_from, p_to = _rolling_previous_equal_span(c_from, c_to)
    span_days = (c_to - c_from).days + 1
    label = "yesterday" if span_days == 1 else "previous period"
    return SummaryWindow(
        c_from,
        c_to,
        p_from,
        p_to,
        comparison_label=label,
    )
