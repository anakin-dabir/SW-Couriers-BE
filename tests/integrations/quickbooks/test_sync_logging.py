"""Unit tests for QuickBooks sync log payload safety, correlation, and edge cases."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from app.integrations.quickbooks.sync_logging import (
    EVENT_CREDIT_NOTE_VOID_CHAIN_QUEUED,
    EVENT_PAYMENT_SYNC_SKIPPED,
    SyncLogContext,
    build_sync_payload,
    correlation_id_for_void_credit_note,
    get_sync_log_context,
    reset_sync_log_context,
    sanitize_sync_payload,
    set_sync_log_context,
)


def test_sanitize_sync_payload_strips_secrets() -> None:
    raw = {
        "access_token": "secret",
        "credit_note_number": "CN-1",
        "nested": {"refresh_token": "x", "amount": Decimal("10.50")},
    }
    clean = sanitize_sync_payload(raw)
    assert clean is not None
    assert "access_token" not in clean
    assert "refresh_token" not in clean.get("nested", {})
    assert clean.get("credit_note_number") == "CN-1"
    assert clean["nested"]["amount"] == "10.50"


def test_sanitize_sync_payload_blocks_token_in_key_name() -> None:
    raw = {"oauth_token_value": "abc", "id": "inv-1"}
    clean = sanitize_sync_payload(raw)
    assert clean is not None
    assert "oauth_token_value" not in clean
    assert clean["id"] == "inv-1"


def test_sanitize_sync_payload_empty_returns_none() -> None:
    assert sanitize_sync_payload({}) is None
    assert sanitize_sync_payload(None) is None


def test_sanitize_sync_payload_truncates_oversized() -> None:
    # Per-field strings cap at 500 chars; exceed 8KB via many keys (json.dumps size check).
    raw = {f"field_{i}": "x" * 300 for i in range(35)}
    clean = sanitize_sync_payload(raw)
    assert clean is not None
    assert clean.get("_truncated") is True


def test_sanitize_sync_payload_json_safe_types() -> None:
    uid = UUID("00000000-0000-4000-8000-000000000099")
    raw = {
        "when": datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
        "day": date(2026, 5, 21),
        "uid": uid,
        "ok": True,
        "n": 1,
    }
    clean = sanitize_sync_payload(raw)
    assert clean is not None
    assert clean["when"].startswith("2026-05-21")
    assert clean["day"] == "2026-05-21"
    assert clean["uid"] == str(uid)


def test_build_sync_payload_merges_context() -> None:
    token = set_sync_log_context(
        SyncLogContext(correlation_id="corr-1", trigger_source="billing.void_credit_note")
    )
    try:
        payload = build_sync_payload(
            business={"credit_note_id": "cn-1"},
            step="reversal_invoice_sync",
        )
        assert payload is not None
        assert payload["correlation_id"] == "corr-1"
        assert payload["trigger_source"] == "billing.void_credit_note"
        assert payload["step"] == "reversal_invoice_sync"
        assert payload["business"]["credit_note_id"] == "cn-1"
    finally:
        reset_sync_log_context(token)


def test_build_sync_payload_explicit_overrides_context() -> None:
    token = set_sync_log_context(SyncLogContext(correlation_id="ctx-corr"))
    try:
        payload = build_sync_payload(correlation_id="explicit-corr")
        assert payload is not None
        assert payload["correlation_id"] == "explicit-corr"
    finally:
        reset_sync_log_context(token)


def test_build_sync_payload_sanitizes_business_secrets() -> None:
    payload = build_sync_payload(
        business={"credit_note_number": "CN-1", "email": "user@example.com"},
    )
    assert payload is not None
    assert payload["business"]["credit_note_number"] == "CN-1"
    assert "email" not in payload["business"]


def test_context_reset_isolates_tasks() -> None:
    token = set_sync_log_context(SyncLogContext(correlation_id="a"))
    reset_sync_log_context(token)
    assert get_sync_log_context() is None


def test_correlation_id_stable_without_version() -> None:
    cid = correlation_id_for_void_credit_note(organization_id="org-1", credit_note_id="cn-1")
    assert cid == "qb:void-cn:org-1:cn-1"


def test_correlation_id_with_version_suffix() -> None:
    cid = correlation_id_for_void_credit_note(organization_id="org-1", credit_note_id="cn-1", version=3)
    assert cid == "qb:void-cn:org-1:cn-1:v3"


def test_event_constants_are_stable_strings() -> None:
    assert EVENT_CREDIT_NOTE_VOID_CHAIN_QUEUED == "CREDIT_NOTE_VOID_CHAIN_QUEUED"
    assert EVENT_PAYMENT_SYNC_SKIPPED == "PAYMENT_SYNC_SKIPPED"
