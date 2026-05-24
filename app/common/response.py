# Response builders and re-exports. Schemas live in app.common.schemas.

from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse

from app.common.schemas import TokenData

__all__ = [
    "error_response",
    "fail_body",
    "fail_response",
    "ok",
]


_UNSET = object()


def ok(data: Any = _UNSET, message: str | None = None, tokens: TokenData | None = None, **kwargs: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"success": True}
    if message is not None:
        body["message"] = message
    if data is not _UNSET:
        body["data"] = data
    if tokens is not None:
        body["tokens"] = tokens
    if kwargs:
        body.update(kwargs)
    return body


def fail_body(
    message: str,
    code: str,
    details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code}
    if details:
        error["details"] = details
    return {"success": False, "message": message, "error": error}


def fail_response(
    status_code: int,
    message: str,
    code: str,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=fail_body(message, code, details))


def error_response(status_code: int, detail: str, code: str) -> JSONResponse:
    return fail_response(status_code, message=detail, code=code)
