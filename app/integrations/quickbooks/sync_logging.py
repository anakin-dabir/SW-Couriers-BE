"""QuickBooks sync log helpers — correlation, safe payloads, request/worker context."""

from __future__ import annotations

import json
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

# Append-only log statuses (stored uppercase in DB rows).
LOG_STATUS_PENDING = "PENDING"
LOG_STATUS_SYNCED = "SYNCED"
LOG_STATUS_FAILED = "FAILED"

# Queue / saga event types (filters + admin UI).
EVENT_CUSTOMER_QUEUED = "CUSTOMER_QUEUED"
EVENT_INVOICE_QUEUED = "INVOICE_QUEUED"
EVENT_CREDIT_NOTE_QUEUED = "CREDIT_NOTE_QUEUED"
EVENT_PAYMENT_QUEUED = "PAYMENT_QUEUED"
EVENT_CREDIT_NOTE_VOID_QUEUED = "CREDIT_NOTE_VOID_QUEUED"
EVENT_CREDIT_NOTE_VOID_CHAIN_QUEUED = "CREDIT_NOTE_VOID_CHAIN_QUEUED"
EVENT_PAYMENT_SYNC_SKIPPED = "PAYMENT_SYNC_SKIPPED"
EVENT_VOID_CHAIN_STEP = "CREDIT_NOTE_VOID_CHAIN_STEP"

_MAX_PAYLOAD_BYTES = 8_192
_MAX_BUSINESS_KEYS = 32
_MAX_STRING_LEN = 500
_MAX_LIST_LEN = 50
_BLOCKED_PAYLOAD_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "password",
        "email",
        "authorization",
        "private_note_full",
    }
)


def _is_blocked_payload_key(key: str) -> bool:
    lowered = str(key).lower()
    return lowered in _BLOCKED_PAYLOAD_KEYS or "token" in lowered or "secret" in lowered


@dataclass(frozen=True, slots=True)
class SyncLogContext:
    """Propagates correlation across nested sync calls within one worker job or HTTP request."""

    correlation_id: str | None = None
    trigger_source: str | None = None
    trigger_entity_id: str | None = None
    saga_step: str | None = None


_sync_log_context: ContextVar[SyncLogContext | None] = ContextVar("qb_sync_log_context", default=None)


def set_sync_log_context(ctx: SyncLogContext | None) -> Token[SyncLogContext | None]:
    return _sync_log_context.set(ctx)


def reset_sync_log_context(token: Token[SyncLogContext | None]) -> None:
    _sync_log_context.reset(token)


def get_sync_log_context() -> SyncLogContext | None:
    return _sync_log_context.get()


def correlation_id_for_void_credit_note(*, organization_id: str, credit_note_id: str, version: int | None = None) -> str:
    """Stable saga id for one void operation (version suffix when job id must be unique per attempt)."""
    base = f"qb:void-cn:{organization_id}:{credit_note_id}"
    if version is None:
        return base
    return f"{base}:v{version}"


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        return value[:_MAX_STRING_LEN]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in list(value.items())[:_MAX_BUSINESS_KEYS]:
            if _is_blocked_payload_key(k):
                continue
            out[str(k)[:80]] = _json_safe_value(v)
        return out
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(v) for v in list(value)[:_MAX_LIST_LEN]]
    return str(value)[:_MAX_STRING_LEN]


def sanitize_sync_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip unsafe/large values; never store tokens, emails, or raw QBO payloads."""
    if not payload:
        return None
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if _is_blocked_payload_key(key):
            continue
        clean[str(key)[:80]] = _json_safe_value(value)
    try:
        encoded = json.dumps(clean, default=str).encode("utf-8")
        if len(encoded) > _MAX_PAYLOAD_BYTES:
            clean["_truncated"] = True
    except Exception:
        return {"_error": "payload_serialization_failed"}
    return clean or None


def build_sync_payload(
    *,
    trigger_source: str | None = None,
    correlation_id: str | None = None,
    trigger_entity_id: str | None = None,
    business: dict[str, Any] | None = None,
    enqueue: dict[str, Any] | None = None,
    step: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    ctx = get_sync_log_context()
    merged: dict[str, Any] = {}
    corr = correlation_id or (ctx.correlation_id if ctx else None)
    if corr:
        merged["correlation_id"] = corr
    src = trigger_source or (ctx.trigger_source if ctx else None)
    if src:
        merged["trigger_source"] = src[:120]
    trig_ent = trigger_entity_id or (ctx.trigger_entity_id if ctx else None)
    if trig_ent:
        merged["trigger_entity_id"] = str(trig_ent)
    saga_step = step or (ctx.saga_step if ctx else None)
    if saga_step:
        merged["step"] = saga_step[:80]
    if business:
        merged["business"] = _json_safe_value(business)
    if enqueue:
        merged["enqueue"] = _json_safe_value(enqueue)
    if extra:
        merged.update(extra)
    return sanitize_sync_payload(merged)
