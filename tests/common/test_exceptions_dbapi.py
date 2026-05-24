from types import SimpleNamespace

from app.common.exceptions import _parse_dbapi_error


def test_parse_dbapi_error_invalid_uuid_shape() -> None:
    exc = SimpleNamespace(orig='invalid input syntax for type uuid: "bad-id"')
    status_code, message, details = _parse_dbapi_error(exc)  # type: ignore[arg-type]

    assert status_code == 422
    assert message == "Invalid ID format."
    assert details == [{"field": "id", "message": "Must be a valid UUID", "type": "value_error.uuid"}]


def test_parse_dbapi_error_invalid_enum_shape() -> None:
    exc = SimpleNamespace(orig='invalid input value for enum payment_provider_enum: "INVALID"')
    status_code, message, details = _parse_dbapi_error(exc)  # type: ignore[arg-type]

    assert status_code == 422
    assert message == "Invalid enum value."
    assert details == [{"field": "unknown", "message": "Must be one of the allowed enum values", "type": "value_error.enum"}]


def test_parse_dbapi_error_unknown_defaults_to_400() -> None:
    exc = SimpleNamespace(orig="some low-level db error")
    status_code, message, details = _parse_dbapi_error(exc)  # type: ignore[arg-type]

    assert status_code == 400
    assert message == "Invalid request."
    assert details is None
