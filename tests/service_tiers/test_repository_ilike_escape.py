"""Unit tests for ILIKE pattern escaping (wildcard injection mitigation)."""

from app.modules.service_tiers.repository import _escape_ilike_pattern


def test_escape_percent_and_underscore() -> None:
    assert _escape_ilike_pattern("100%off") == "100\\%off"
    assert _escape_ilike_pattern("a_b") == "a\\_b"


def test_escape_literal_backslash_before_percent() -> None:
    assert _escape_ilike_pattern("x\\y%z") == "x\\\\y\\%z"
