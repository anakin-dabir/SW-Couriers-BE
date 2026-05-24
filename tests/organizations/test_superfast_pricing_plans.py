"""Unit tests for Superfast pricing plan helpers."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.common.exceptions import ValidationError
from app.modules.organizations.superfast_tier import (
    build_standard_superfast_plan_entry,
    ensure_superfast_in_pricing_plans,
    reject_superfast_deselect,
    validate_superfast_plan_constraints,
)
from app.modules.service_tiers.constants import SUPERFAST_TIER_NAME


def _superfast_tier(*, tier_id: str = "sf-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=tier_id,
        tier_name=SUPERFAST_TIER_NAME,
        duration_days=1,
        base_price=Decimal("0"),
        price_per_package=Decimal("125.00"),
        price_per_kg=Decimal("0"),
        color="#E63946",
        icon="bolt",
    )


def test_build_standard_superfast_plan_entry() -> None:
    entry = build_standard_superfast_plan_entry(_superfast_tier())
    assert entry["plain_name"] == SUPERFAST_TIER_NAME
    assert entry["plain_type"] == "standard"
    assert entry["permitted"] is True
    assert entry["price_per_package"] == "125.00"
    assert entry["base_price"] == "125.00"
    assert entry["price_per_kg"] == "0.00"
    assert entry["days"] == 1


def test_ensure_superfast_appends_when_missing() -> None:
    tier = _superfast_tier()
    out = ensure_superfast_in_pricing_plans([{"id_price_tier": "other", "permitted": True}], tier)
    assert len(out) == 2
    assert any(str(p["id_price_tier"]) == "sf-1" for p in out)


def test_ensure_superfast_forces_permitted_true() -> None:
    tier = _superfast_tier()
    out = ensure_superfast_in_pricing_plans(
        [{"id_price_tier": "sf-1", "permitted": False, "plain_type": "custom", "price_per_package": "10"}],
        tier,
    )
    assert out[0]["permitted"] is True


def test_reject_superfast_deselect() -> None:
    with pytest.raises(ValidationError, match="cannot be deselected"):
        reject_superfast_deselect(
            [{"id_price_tier": "sf-1", "permitted": False}],
            superfast_id="sf-1",
        )


def test_validate_superfast_rejects_deselect() -> None:
    with pytest.raises(ValidationError, match="cannot be deselected"):
        validate_superfast_plan_constraints(
            [{"id_price_tier": "sf-1", "permitted": False}],
            superfast_id="sf-1",
        )


def test_validate_superfast_rejects_missing() -> None:
    with pytest.raises(ValidationError, match="must be included"):
        validate_superfast_plan_constraints([], superfast_id="sf-1")
