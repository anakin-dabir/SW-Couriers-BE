"""Unit tests for route navigation fingerprint helper."""

from __future__ import annotations

from app.modules.planning.route_navigation import compute_route_navigation_fingerprint


def test_navigation_fingerprint_order_independent_of_input_order() -> None:
    a = compute_route_navigation_fingerprint(sequences_and_route_stop_ids=[(2, "b"), (1, "a")])
    b = compute_route_navigation_fingerprint(sequences_and_route_stop_ids=[(1, "a"), (2, "b")])
    assert a == b


def test_navigation_fingerprint_changes_when_sequence_changes() -> None:
    a = compute_route_navigation_fingerprint(sequences_and_route_stop_ids=[(1, "a"), (2, "b")])
    b = compute_route_navigation_fingerprint(sequences_and_route_stop_ids=[(1, "b"), (2, "a")])
    assert a != b
