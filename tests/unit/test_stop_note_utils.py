"""Unit tests for stop note type normalization and package_ids parsing."""

from __future__ import annotations

import uuid

import pytest

from app.common.exceptions import ValidationError
from app.modules.orders.enums import STOP_NOTE_PACKAGE_IDS_MAX, StopNoteType
from app.modules.orders.stop_note_utils import (
    normalize_stop_note_type,
    parse_and_validate_package_ids_for_note,
    validate_stop_note_type_allowed,
)


def test_normalize_stop_note_type_aliases() -> None:
    assert normalize_stop_note_type("admin_note") == StopNoteType.ADMIN.value
    assert normalize_stop_note_type("CLIENT") == StopNoteType.CUSTOMER.value
    assert normalize_stop_note_type("client_note") == StopNoteType.CUSTOMER.value
    assert normalize_stop_note_type("PACKAGE_ISSUE_NOTE") == StopNoteType.PACKAGE_ISSUE_NOTE.value


def test_normalize_stop_note_type_empty_raises() -> None:
    with pytest.raises(ValidationError, match="note_type"):
        normalize_stop_note_type("   ")


def test_parse_package_ids_non_issue_rejects_non_empty() -> None:
    u = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    with pytest.raises(ValidationError) as exc:
        parse_and_validate_package_ids_for_note(
            note_type=StopNoteType.ADMIN.value,
            package_ids=[u],
        )
    assert exc.value.code == "PACKAGE_IDS_NOT_ALLOWED"


def test_parse_package_ids_dedupes() -> None:
    u = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    out = parse_and_validate_package_ids_for_note(
        note_type=StopNoteType.PACKAGE_ISSUE_NOTE.value,
        package_ids=[u, u, f" {u} "],
    )
    assert out == [u]


def test_parse_package_ids_max_limit() -> None:
    ids = [str(uuid.uuid4()) for _ in range(STOP_NOTE_PACKAGE_IDS_MAX + 1)]
    with pytest.raises(ValidationError) as exc:
        parse_and_validate_package_ids_for_note(
            note_type=StopNoteType.PACKAGE_ISSUE_NOTE.value,
            package_ids=ids,
        )
    assert exc.value.code == "PACKAGE_IDS_LIMIT"


def test_validate_stop_note_type_strict() -> None:
    validate_stop_note_type_allowed("HANDOVER", strict=False)
    with pytest.raises(ValidationError) as exc:
        validate_stop_note_type_allowed("HANDOVER", strict=True)
    assert exc.value.code == "INVALID_STOP_NOTE_TYPE"


def test_parse_package_ids_invalid_uuid() -> None:
    with pytest.raises(ValidationError, match="Invalid package id"):
        parse_and_validate_package_ids_for_note(
            note_type=StopNoteType.PACKAGE_ISSUE_NOTE.value,
            package_ids=["not-a-uuid"],
        )


def test_parse_package_ids_empty_for_issue_returns_none() -> None:
    assert (
        parse_and_validate_package_ids_for_note(
            note_type=StopNoteType.PACKAGE_ISSUE_NOTE.value,
            package_ids=[],
        )
        is None
    )


def test_assert_stop_flow_pickup_rejects_admin() -> None:
    from types import SimpleNamespace

    from app.modules.orders.stop_note_utils import assert_stop_note_type_allowed_for_stop_flow

    stop = SimpleNamespace(stop_flow="PICKUP")
    with pytest.raises(ValidationError) as exc:
        assert_stop_note_type_allowed_for_stop_flow(note_type=StopNoteType.ADMIN.value, stop=stop)
    assert exc.value.code == "STOP_NOTE_TYPE_NOT_ALLOWED_FOR_FLOW"


def test_assert_stop_flow_pickup_allows_customer() -> None:
    from types import SimpleNamespace

    from app.modules.orders.stop_note_utils import assert_stop_note_type_allowed_for_stop_flow

    assert_stop_note_type_allowed_for_stop_flow(
        note_type=StopNoteType.CUSTOMER.value,
        stop=SimpleNamespace(stop_flow="PICKUP"),
    )


def test_assert_stop_flow_missing_allows_all() -> None:
    from types import SimpleNamespace

    from app.modules.orders.stop_note_utils import assert_stop_note_type_allowed_for_stop_flow

    assert_stop_note_type_allowed_for_stop_flow(
        note_type=StopNoteType.ADMIN.value,
        stop=SimpleNamespace(),
    )
