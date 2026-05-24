"""Unit tests for pure GLOBAL + ORG effective tier merge (no database)."""

from types import SimpleNamespace

import pytest

from app.modules.service_tiers.effective_merge import merge_effective_service_tiers
from app.modules.service_tiers.enums import ServiceTierScopeType


def _tier(tid: str, name: str, aud: str) -> SimpleNamespace:
    return SimpleNamespace(id=tid, tier_name=name, available_for=aud)


def test_merge_org_override_replaces_global_same_key() -> None:
    g = _tier("g1", "Standard", "BOTH")
    o = _tier("o1", "Standard", "BOTH")
    out = merge_effective_service_tiers([g], [o])
    assert len(out) == 1
    assert out[0]["tier"].id == "o1"
    assert out[0]["is_override"] is True
    assert out[0]["global_tier_id"] == "g1"
    assert out[0]["source_scope_type"] == ServiceTierScopeType.ORG.value


def test_merge_global_only_when_no_org_match() -> None:
    g = _tier("g1", "Economy", "CUSTOMER_B2C")
    out = merge_effective_service_tiers([g], [])
    assert len(out) == 1
    assert out[0]["tier"].id == "g1"
    assert out[0]["is_override"] is False
    assert out[0]["source_scope_type"] == ServiceTierScopeType.GLOBAL.value
    assert out[0]["global_tier_id"] == "g1"


def test_merge_appends_org_only_tier() -> None:
    g = _tier("g1", "Standard", "BOTH")
    o_only = _tier("o2", "VIP", "CUSTOMER_B2B")
    out = merge_effective_service_tiers([g], [o_only])
    assert len(out) == 2
    keys = {(row["tier"].tier_name, row["tier"].available_for) for row in out}
    assert ("Standard", "BOTH") in keys
    assert ("VIP", "CUSTOMER_B2B") in keys
    vip = next(r for r in out if r["tier"].tier_name == "VIP")
    assert vip["is_override"] is False
    assert vip["global_tier_id"] is None


def test_merge_multiple_globals_one_org_override() -> None:
    g1 = _tier("g1", "A", "BOTH")
    g2 = _tier("g2", "B", "BOTH")
    o = _tier("o1", "A", "BOTH")
    out = merge_effective_service_tiers([g1, g2], [o])
    assert len(out) == 2
    by_name = {r["tier"].tier_name: r for r in out}
    assert by_name["A"]["tier"].id == "o1"
    assert by_name["A"]["is_override"] is True
    assert by_name["B"]["tier"].id == "g2"
    assert by_name["B"]["is_override"] is False


@pytest.mark.parametrize(
    "g_rows,o_rows,expected_len",
    [
        ([], [], 0),
        ([], [_tier("o1", "Only", "BOTH")], 1),
    ],
)
def test_merge_empty_and_org_only_globals_empty(
    g_rows: list, o_rows: list, expected_len: int
) -> None:
    assert len(merge_effective_service_tiers(g_rows, o_rows)) == expected_len
