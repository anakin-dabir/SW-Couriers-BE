"""Stop note type normalization and ``package_ids`` validation."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ValidationError
from app.core.config import get_settings
from app.modules.orders.enums import STOP_NOTE_PACKAGE_IDS_MAX, StopNoteType

# Admin create/update aliases → persisted value
_STOP_NOTE_TYPE_ALIASES: dict[str, str] = {
    "ADMIN_NOTE": StopNoteType.ADMIN.value,
    "CLIENT": StopNoteType.CUSTOMER.value,
    "CLIENT_NOTE": StopNoteType.CUSTOMER.value,
}

_PERSISTED = {e.value for e in StopNoteType}


def normalize_stop_note_type(raw: str) -> str:
    """Strip, upper, map aliases → persisted ``StopNoteType`` value."""
    key = (raw or "").strip().upper()
    if not key:
        raise ValidationError("note_type is required")
    mapped = _STOP_NOTE_TYPE_ALIASES.get(key, key)
    return mapped


def validate_stop_note_type_allowed(persisted: str, *, strict: bool) -> None:
    """Reject unknown types when strict mode is on (after alias normalization)."""
    if persisted in _PERSISTED:
        return
    if strict:
        raise ValidationError(
            f"Invalid note_type '{persisted}'. Allowed: {', '.join(sorted(_PERSISTED))}",
            code="INVALID_STOP_NOTE_TYPE",
        )


def parse_and_validate_package_ids_for_note(
    *,
    note_type: str,
    package_ids: list[str] | None,
) -> list[str] | None:
    """Normalize package_ids for persistence. For non-issue notes, must be empty."""
    if package_ids is None:
        if note_type == StopNoteType.PACKAGE_ISSUE_NOTE.value:
            return None
        return None

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in package_ids:
        s = (raw or "").strip()
        if not s:
            continue
        try:
            UUID(s)
        except ValueError as err:
            raise ValidationError(f"Invalid package id UUID: {raw!r}") from err
        if s not in seen:
            seen.add(s)
            cleaned.append(s)

    if note_type != StopNoteType.PACKAGE_ISSUE_NOTE.value:
        if cleaned:
            raise ValidationError("package_ids is only allowed for PACKAGE_ISSUE_NOTE", code="PACKAGE_IDS_NOT_ALLOWED")
        return None

    if len(cleaned) > STOP_NOTE_PACKAGE_IDS_MAX:
        raise ValidationError(
            f"At most {STOP_NOTE_PACKAGE_IDS_MAX} package_ids allowed",
            code="PACKAGE_IDS_LIMIT",
        )

    return cleaned if cleaned else None


async def assert_package_ids_belong_to_stop(
    session: AsyncSession,
    *,
    delivery_stop_id: str,
    order_id: str,
    package_ids: list[str],
) -> None:
    if not package_ids:
        return
    from app.modules.orders.models import Package

    stmt = select(Package.id).where(
        Package.id.in_(package_ids),
        Package.delivery_stop_id == delivery_stop_id,
        Package.order_id == order_id,
    )
    rows = (await session.execute(stmt)).scalars().all()
    if len(rows) != len(package_ids):
        raise ValidationError(
            "One or more package_ids do not belong to this stop",
            code="INVALID_PACKAGE_IDS_FOR_STOP",
        )


def is_strict_stop_note_types() -> bool:
    return bool(get_settings().STRICT_STOP_NOTE_TYPES)


def assert_stop_note_type_allowed_for_stop_flow(*, note_type: str, stop: object) -> None:
    """Enforce pickup-only CUSTOMER when ``stop_flow`` is present (``PICKUP``)."""
    flow = getattr(stop, "stop_flow", None)
    if flow is None:
        return
    flow_s = str(flow).strip().upper()
    if flow_s == "PICKUP" and note_type != StopNoteType.CUSTOMER.value:
        raise ValidationError(
            "Only CUSTOMER stop notes are allowed on pickup stops",
            code="STOP_NOTE_TYPE_NOT_ALLOWED_FOR_FLOW",
        )


async def batch_package_ids_for_stop_notes(
    session: AsyncSession,
    *,
    delivery_stop_id: str,
    order_id: str,
    notes: Sequence[Any],
) -> dict[str, list[str]]:
    """For each note id, return validated package UUID lists (one DB round-trip)."""
    from app.modules.orders.models import Package

    per_note: dict[str, list[str]] = {}
    all_candidates: set[str] = set()
    note_candidates: dict[str, list[str]] = {}

    for n in notes:
        nid = str(getattr(n, "id"))
        raw = getattr(n, "package_ids", None)
        if raw is None or not isinstance(raw, list):
            per_note[nid] = []
            continue
        cands: list[str] = []
        for x in raw:
            if isinstance(x, str) and x.strip():
                try:
                    UUID(x.strip())
                    cands.append(x.strip())
                except ValueError:
                    continue
        note_candidates[nid] = cands
        all_candidates.update(cands)

    if not all_candidates:
        return {str(getattr(n, "id")): [] for n in notes}

    stmt = select(Package.id).where(
        Package.id.in_(all_candidates),
        Package.delivery_stop_id == delivery_stop_id,
        Package.order_id == order_id,
    )
    valid = set((await session.execute(stmt)).scalars().all())

    for nid, cands in note_candidates.items():
        per_note[nid] = sorted([p for p in cands if p in valid])

    for n in notes:
        nid = str(getattr(n, "id"))
        if nid not in per_note:
            per_note[nid] = []

    return per_note
