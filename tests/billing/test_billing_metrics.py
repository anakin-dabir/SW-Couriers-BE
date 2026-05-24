from datetime import date
from decimal import Decimal

from app.modules.billing.metrics import (
    BillingOverviewPeriodPreset,
    pct_change,
    resolve_billing_overview_window,
    split_vat_from_gross,
)


def test_resolve_billing_overview_window_yesterday() -> None:
    window, meta = resolve_billing_overview_window(
        period=BillingOverviewPeriodPreset.YESTERDAY,
        today=date(2026, 3, 17),
    )
    assert window.current_from == date(2026, 3, 16)
    assert window.current_to == date(2026, 3, 16)
    assert meta.period_start == date(2026, 3, 16)


def test_pct_change() -> None:
    assert pct_change(Decimal("120"), Decimal("100")) == Decimal("20.00")


def test_split_vat_from_gross() -> None:
    subtotal, vat, total = split_vat_from_gross(Decimal("120.00"))
    assert total == Decimal("120.00")
    assert subtotal + vat == total
