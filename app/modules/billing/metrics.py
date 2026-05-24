"""Canonical billing metric definitions (single source of truth for overview KPIs)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.modules.orders.enums import SummaryPeriodPreset
from app.modules.orders.utils import SummaryWindow, resolve_summary_window

DEFINITIONS_VERSION = "1.0"
DEFAULT_VAT_RATE = Decimal("20.0")


@dataclass(frozen=True, slots=True)
class BillingPeriodMeta:
    period_start: date
    period_end: date
    prior_period_start: date
    prior_period_end: date
    comparison_label: str
    timezone: str
    definitions_version: str


class BillingOverviewPeriodPreset:
    """API period presets for org billing overview (includes yesterday)."""

    TODAY = "today"
    YESTERDAY = "yesterday"
    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"


def resolve_billing_overview_window(
    *,
    period: str | None,
    today: date | None = None,
) -> tuple[SummaryWindow, BillingPeriodMeta]:
    anchor = today or date.today()
    preset = (period or BillingOverviewPeriodPreset.LAST_30_DAYS).strip().lower()

    if preset == BillingOverviewPeriodPreset.YESTERDAY:
        y = anchor - timedelta(days=1)
        window = SummaryWindow(
            current_from=y,
            current_to=y,
            previous_from=y - timedelta(days=1),
            previous_to=y - timedelta(days=1),
            comparison_label="day before yesterday",
        )
    else:
        mapping = {
            BillingOverviewPeriodPreset.TODAY: SummaryPeriodPreset.TODAY,
            BillingOverviewPeriodPreset.LAST_7_DAYS: SummaryPeriodPreset.LAST_7_DAYS,
            BillingOverviewPeriodPreset.LAST_30_DAYS: SummaryPeriodPreset.LAST_30_DAYS,
        }
        summary_preset = mapping.get(preset, SummaryPeriodPreset.LAST_30_DAYS)
        window = resolve_summary_window(period=summary_preset, date_from=None, date_to=None, today=anchor)

    meta = BillingPeriodMeta(
        period_start=window.current_from,
        period_end=window.current_to,
        prior_period_start=window.previous_from,
        prior_period_end=window.previous_to,
        comparison_label=window.comparison_label,
        timezone="UTC",
        definitions_version=DEFINITIONS_VERSION,
    )
    return window, meta


def pct_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous == 0:
        return None if current == 0 else Decimal("100")
    return ((current - previous) / previous * Decimal("100")).quantize(Decimal("0.01"))


def split_vat_from_gross(gross: Decimal, vat_rate: Decimal = DEFAULT_VAT_RATE) -> tuple[Decimal, Decimal, Decimal]:
    """Return (subtotal, vat_amount, total) where total == gross."""
    rate = vat_rate / Decimal("100")
    if rate <= 0:
        total = gross.quantize(Decimal("0.01"))
        return total, Decimal("0.00"), total
    subtotal = (gross / (Decimal("1") + rate)).quantize(Decimal("0.01"))
    vat_amount = (gross - subtotal).quantize(Decimal("0.01"))
    return subtotal, vat_amount, gross.quantize(Decimal("0.01"))


def map_payment_provider_to_chart_bucket(provider: str) -> str:
    p = (provider or "").upper()
    if p == "BRAINTREE":
        return "CARD"
    if p == "BANK_TRANSFER":
        return "BANK_TRANSFER"
    return "CASH"
