"""Pydantic validation for stop note request bodies (Swagger-aligned rules)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.orders.enums import StopNoteType
from app.modules.orders.v1.schemas import StopNoteCreateRequest, StopNoteUpdateRequest


def test_create_accepts_three_ui_aligned_examples() -> None:
    admin = StopNoteCreateRequest(
        note_type="ADMIN",
        message="Call customer 10 minutes before arrival.",
        is_blocking=True,
        sort_order=0,
    )
    assert admin.note_type == "ADMIN"

    customer = StopNoteCreateRequest(
        note_type="CUSTOMER",
        message="Leave parcel with neighbour at number 12 if unavailable.",
    )
    assert customer.package_ids is None

    issue = StopNoteCreateRequest(
        note_type="PACKAGE_ISSUE_NOTE",
        message="Parcel received with damaged outer packaging.",
        package_ids=[
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ],
    )
    assert len(issue.package_ids or []) == 2


def test_create_rejects_package_ids_for_customer() -> None:
    with pytest.raises(ValidationError) as exc:
        StopNoteCreateRequest(
            note_type="CUSTOMER",
            message="x",
            package_ids=["11111111-1111-1111-1111-111111111111"],
        )
    assert "PACKAGE_ISSUE_NOTE" in str(exc.value)


def test_create_rejects_package_ids_for_admin() -> None:
    with pytest.raises(ValidationError):
        StopNoteCreateRequest(
            note_type="ADMIN",
            message="x",
            package_ids=["11111111-1111-1111-1111-111111111111"],
        )


def test_create_allows_alias_types_without_package_ids() -> None:
    body = StopNoteCreateRequest(note_type="ADMIN_NOTE", message="ops", package_ids=None)
    assert body.note_type == "ADMIN_NOTE"
    c = StopNoteCreateRequest(note_type="CLIENT_NOTE", message="hello")
    assert c.note_type == "CLIENT_NOTE"


def test_update_rejects_package_ids_when_changing_to_customer() -> None:
    with pytest.raises(ValidationError):
        StopNoteUpdateRequest(
            note_type="CUSTOMER",
            message="x",
            package_ids=["11111111-1111-1111-1111-111111111111"],
        )


def test_update_allows_package_ids_for_package_issue() -> None:
    u = StopNoteUpdateRequest(
        note_type=StopNoteType.PACKAGE_ISSUE_NOTE.value,
        package_ids=["11111111-1111-1111-1111-111111111111"],
    )
    assert u.package_ids is not None


def test_update_allows_message_only() -> None:
    StopNoteUpdateRequest(message="New text only")
