from __future__ import annotations

import uuid

import pytest

from app.common.validators import is_uuid_string, normalize_optional_uuid


def test_is_uuid_string_accepts_canonical_uuid() -> None:
    value = str(uuid.uuid4())
    assert is_uuid_string(value) is True


@pytest.mark.parametrize("value", ["", "DRAFT-000001", "not-a-uuid", None])
def test_is_uuid_string_rejects_invalid(value: str | None) -> None:
    assert is_uuid_string(value) is False


def test_normalize_optional_uuid_blank_becomes_none() -> None:
    assert normalize_optional_uuid("   ", field="pickup_address_id") is None


def test_normalize_optional_uuid_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="pickup_address_id must be a valid UUID"):
        normalize_optional_uuid("DRAFT-000001", field="pickup_address_id")
