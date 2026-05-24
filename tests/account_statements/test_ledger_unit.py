"""Unit tests for account statement ledger helpers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.common.exceptions import ValidationError
from app.modules.account_statements.ledger import _aging_bucket, compute_content_signature
from app.modules.account_statements.validation import validate_period


def test_validate_period_rejects_inverted_range() -> None:
    with pytest.raises(ValidationError, match="period_start"):
        validate_period(date(2026, 2, 1), date(2026, 1, 1))


def test_validate_period_rejects_future_end() -> None:
    with pytest.raises(ValidationError, match="future"):
        validate_period(date(2026, 1, 1), date(2099, 1, 1), today=date(2026, 5, 18))


def test_validate_period_rejects_span_over_max() -> None:
    start = date(2024, 1, 1)
    end = date(2025, 6, 1)
    with pytest.raises(ValidationError, match="366"):
        validate_period(start, end, today=date(2026, 1, 1))


def test_aging_bucket_boundaries() -> None:
    assert _aging_bucket(0) is None
    assert _aging_bucket(1) == "days_1_30"
    assert _aging_bucket(30) == "days_1_30"
    assert _aging_bucket(31) == "days_31_60"
    assert _aging_bucket(60) == "days_31_60"
    assert _aging_bucket(61) == "days_61_90"
    assert _aging_bucket(90) == "days_61_90"
    assert _aging_bucket(91) == "days_90_plus"


def test_content_signature_changes_with_toggles() -> None:
    base = dict(
        organization_id="org-1",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        template_version="v1",
    )
    sig_a = compute_content_signature(
        **base,
        include_line_item_detail=False,
        include_credit_notes=True,
        include_payment_history=True,
    )
    sig_b = compute_content_signature(
        **base,
        include_line_item_detail=True,
        include_credit_notes=True,
        include_payment_history=True,
    )
    assert sig_a != sig_b
