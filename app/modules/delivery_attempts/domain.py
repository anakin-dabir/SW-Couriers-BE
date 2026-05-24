from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from app.common.exceptions import ValidationError

DEFAULT_ATTEMPTS_COUNT = 3


def default_fee_entries(max_attempts: int = DEFAULT_ATTEMPTS_COUNT) -> list[dict[str, Any]]:
    """Build a zero-fee contiguous attempt schedule."""
    return [{"attempt": i, "fee": "0.00"} for i in range(1, max_attempts + 1)]


def _entry_value(entry: Any, key: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(key)
    return getattr(entry, key, None)


def _coerce_fee(value: Any, field_name: str) -> Decimal:
    try:
        fee = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} contains an invalid fee value: {value!r}.") from exc
    if fee < 0:
        raise ValidationError(f"{field_name} contains a negative fee: {fee}.")
    return fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _coerce_entries(entries: list[Any], field_name: str) -> list[dict[str, Any]]:
    coerced: list[dict[str, Any]] = []
    seen_attempts: set[int] = set()
    for idx, entry in enumerate(entries):
        attempt_raw = _entry_value(entry, "attempt")
        fee_raw = _entry_value(entry, "fee")
        if attempt_raw is None or fee_raw is None:
            raise ValidationError(f"{field_name}[{idx}] must include both 'attempt' and 'fee'.")
        try:
            attempt = int(attempt_raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"{field_name}[{idx}].attempt must be an integer.") from exc
        if attempt < 1:
            raise ValidationError(f"{field_name}[{idx}].attempt must be >= 1.")
        if attempt in seen_attempts:
            raise ValidationError(f"{field_name} contains duplicate attempt number {attempt}.")
        seen_attempts.add(attempt)

        fee = _coerce_fee(fee_raw, field_name)
        coerced.append({"attempt": attempt, "fee": str(fee)})
    return coerced


def validate_strict_attempt_fees(entries: list[Any], max_attempts: int, field_name: str) -> list[dict[str, Any]]:
    """Validate exact 1..N attempt schedule and return normalized serialized entries."""
    if max_attempts < 1:
        raise ValidationError(f"{field_name} requires max_attempts >= 1.")
    normalized = _coerce_entries(entries, field_name)
    if len(normalized) != max_attempts:
        raise ValidationError(f"{field_name} must have exactly {max_attempts} entries, got {len(normalized)}.")
    expected = list(range(1, max_attempts + 1))
    actual = sorted(item["attempt"] for item in normalized)
    if actual != expected:
        raise ValidationError(f"{field_name} attempt numbers must be sequential from 1 to {max_attempts}.")
    # Preserve deterministic order in persisted JSON.
    return sorted(normalized, key=lambda item: item["attempt"])


def compact_attempt_fees(entries: list[Any], field_name: str) -> list[dict[str, Any]]:
    """Compact sparse attempt numbers into contiguous 1..N sequence by attempt order."""
    normalized = _coerce_entries(entries, field_name)
    if not normalized:
        raise ValidationError(f"{field_name} must contain at least one attempt.")
    ordered = sorted(normalized, key=lambda item: item["attempt"])
    compacted: list[dict[str, Any]] = []
    for idx, item in enumerate(ordered, start=1):
        compacted.append({"attempt": idx, "fee": item["fee"]})
    return compacted
