# App exceptions and handlers. Response building lives in app.common.response.

from __future__ import annotations

import re
from typing import Any

import structlog
from fastapi import status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.common.enums import ErrorCode, LogEvent
from app.common.response import fail_response

logger = structlog.get_logger()


class AppError(Exception):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: ErrorCode | str = ErrorCode.APP_ERROR

    def __init__(self, message: str, code: ErrorCode | str | None = None) -> None:
        self.message = message
        if code is not None:
            self.code = code
        super().__init__(message)


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = ErrorCode.NOT_FOUND

    def __init__(self, resource: str = "Resource", id: str | None = None) -> None:
        detail = f"{resource} not found" if not id else f"{resource} with id '{id}' not found"
        super().__init__(message=detail)


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = ErrorCode.CONFLICT

    def __init__(self, message: str = "Resource was modified by another request") -> None:
        super().__init__(message=message)


class IdempotencyConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = ErrorCode.IDEMPOTENCY_CONFLICT

    def __init__(self, message: str = "Duplicate request or request already in progress") -> None:
        super().__init__(message=message)


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = ErrorCode.FORBIDDEN

    def __init__(self, message: str = "You do not have permission to perform this action") -> None:
        super().__init__(message=message)


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = ErrorCode.VALIDATION_ERROR

    def __init__(
        self,
        message: str,
        details: list[dict[str, Any]] | None = None,
        *,
        code: ErrorCode | str | None = None,
    ) -> None:
        super().__init__(message=message, code=code)
        self.details = details


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = ErrorCode.AUTHENTICATION_ERROR

    def __init__(self, message: str = "Invalid credentials") -> None:
        super().__init__(message=message)


class InvalidStateTransitionError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = ErrorCode.INVALID_STATE_TRANSITION

    def __init__(self, current_state: str, target_state: str, entity: str = "Resource") -> None:
        message = f"Cannot transition {entity} from '{current_state}' to '{target_state}'"
        super().__init__(message=message)


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = ErrorCode.RATE_LIMIT_EXCEEDED

    def __init__(self, message: str = "Too many requests. Please try again later.", retry_after: int | None = None) -> None:
        super().__init__(message=message)
        self.retry_after = retry_after


class APIKeyError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = ErrorCode.API_KEY_ERROR

    def __init__(self, message: str, code: ErrorCode | str = ErrorCode.API_KEY_ERROR) -> None:
        super().__init__(message=message, code=code)


class StorageProviderError(AppError):
    """Generic error for external storage providers (Cloudflare R2, Images, etc.).

    Surfaces as a 500 with a stable error code while logging full context server-side.
    The message is intentionally generic to avoid leaking provider details to clients.
    """

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: ErrorCode | str = "STORAGE_PROVIDER_ERROR"

    def __init__(self, message: str = "Storage provider error") -> None:
        super().__init__(message=message, code=self.code)


class PaymentProviderUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = ErrorCode.PAYMENT_PROVIDER_UNAVAILABLE

    def __init__(
        self,
        message: str = "Payment service is temporarily unavailable. Please try again.",
    ) -> None:
        super().__init__(message=message, code=ErrorCode.PAYMENT_PROVIDER_UNAVAILABLE)


# Validation error formatting (used by validation handler)
def _format_loc(loc: tuple | list) -> str:
    parts = [str(p) for p in loc if p != "body"]
    if parts:
        return ".".join(parts)
    if loc and "body" in loc:
        return "body"
    return "unknown"


def _sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for err in errors:
        msg = err.get("msg", "Invalid value")
        if isinstance(msg, str) and msg.startswith("Value error, "):
            msg = msg.replace("Value error, ", "", 1)
        out.append(
            {
                "field": _format_loc(err.get("loc", ())),
                "message": msg,
                "type": err.get("type", "value_error"),
            }
        )
    return out


# FK constraint format: <table>_<column>_fkey
_FK_RE = re.compile(r'constraint "(\w+?)_(\w+?)_fkey"')
# Unique constraint format: <table>_<column>_key or named via UniqueConstraint
_UNIQUE_RE = re.compile(r'constraint "(\w+)"')
_NOT_NULL_RE = re.compile(r'null value in column "(\w+)"')


def _parse_integrity_error(exc: IntegrityError) -> tuple[int, str]:
    orig = str(exc.orig) if exc.orig else str(exc)

    if "ForeignKeyViolation" in orig or "foreign key" in orig.lower():
        match = _FK_RE.search(orig)
        if match:
            _table, column = match.group(1), match.group(2)
            friendly_col = column.replace("_id", "").replace("_", " ")
            return (
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Invalid {friendly_col}: the referenced {friendly_col} does not exist.",
            )
        return status.HTTP_422_UNPROCESSABLE_CONTENT, "A referenced record does not exist."

    if "UniqueViolation" in orig or "unique constraint" in orig.lower() or "duplicate key" in orig.lower():
        match = _UNIQUE_RE.search(orig)
        detail_match = re.search(r"Key \((\w+)\)=\((.+?)\)", orig)
        if detail_match:
            column = detail_match.group(1).replace("_", " ")
            value = detail_match.group(2)
            return (
                status.HTTP_409_CONFLICT,
                f"A record with this {column} ('{value}') already exists.",
            )
        if match:
            return status.HTTP_409_CONFLICT, f"Duplicate value violates unique constraint '{match.group(1)}'."
        return status.HTTP_409_CONFLICT, "A record with this value already exists."

    if "NotNullViolation" in orig or "not-null constraint" in orig.lower():
        match = _NOT_NULL_RE.search(orig)
        if match:
            column = match.group(1).replace("_", " ")
            return status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{column}' is required and cannot be empty."
        return status.HTTP_422_UNPROCESSABLE_CONTENT, "A required field is missing."

    if "CheckViolation" in orig or "check constraint" in orig.lower():
        return status.HTTP_422_UNPROCESSABLE_CONTENT, "A value failed a database constraint check."

    return status.HTTP_409_CONFLICT, "The request conflicts with the current state of the database."


def _parse_dbapi_error(exc: DBAPIError) -> tuple[int, str, list[dict[str, Any]] | None]:
    """Map driver-level errors to a user-facing status/message/details shape."""
    orig = str(getattr(exc, "orig", "") or "")
    low = orig.lower()

    if "invalid uuid" in low:
        return (
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Invalid ID format.",
            [{"field": "id", "message": "Must be a valid UUID", "type": "value_error.uuid"}],
        )

    if "invalid input syntax for type uuid" in low:
        return (
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Invalid ID format.",
            [{"field": "id", "message": "Must be a valid UUID", "type": "value_error.uuid"}],
        )

    if "invalid input value for enum" in low:
        return (
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Invalid enum value.",
            [{"field": "unknown", "message": "Must be one of the allowed enum values", "type": "value_error.enum"}],
        )

    if "invalid input syntax" in low or "dataerror" in low:
        return (
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Invalid request payload.",
            [{"field": "unknown", "message": "Input format is invalid", "type": "value_error"}],
        )

    return status.HTTP_400_BAD_REQUEST, "Invalid request.", None


def register_exception_handlers(app: Any) -> None:  # noqa: ANN001
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:  # noqa: ARG001
        details = getattr(exc, "details", None)
        response = fail_response(exc.status_code, message=exc.message, code=str(exc.code), details=details)
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            response.headers["Retry-After"] = str(retry_after)
        return response

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        status_code, message = _parse_integrity_error(exc)
        code = ErrorCode.VALIDATION_ERROR if status_code == 422 else ErrorCode.CONFLICT
        logger.warning(
            "db.integrity_error",
            path=request.url.path,
            method=request.method,
            detail=str(exc.orig),
        )
        return fail_response(status_code, message=message, code=str(code))

    @app.exception_handler(DBAPIError)
    async def dbapi_error_handler(request: Request, exc: DBAPIError) -> JSONResponse:
        status_code, message, details = _parse_dbapi_error(exc)
        code = ErrorCode.VALIDATION_ERROR if status_code == 422 else ErrorCode.HTTP_ERROR
        logger.warning(
            "db.dbapi_error",
            path=request.url.path,
            method=request.method,
            detail=str(getattr(exc, "orig", exc)),
        )
        return fail_response(status_code, message=message, code=str(code), details=details)

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception(
            "db.sqlalchemy_error",
            path=request.url.path,
            method=request.method,
            exc_info=exc,
        )
        return fail_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Internal server error",
            code=str(ErrorCode.INTERNAL_ERROR),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:  # noqa: ARG001
        return fail_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            message="Validation error",
            code=str(ErrorCode.VALIDATION_ERROR),
            details=_sanitize_validation_errors(list(exc.errors())),
        )

    @app.exception_handler(PydanticValidationError)
    async def pydantic_validation_error_handler(request: Request, exc: PydanticValidationError) -> JSONResponse:  # noqa: ARG001
        raw = [{"loc": e.get("loc", ()), "msg": e.get("msg", "Invalid value"), "type": e.get("type", "value_error")} for e in exc.errors()]
        return fail_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            message="Validation error",
            code=str(ErrorCode.VALIDATION_ERROR),
            details=_sanitize_validation_errors(raw),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:  # noqa: ARG001
        message = exc.detail if isinstance(exc.detail, str) else "Request error"
        code_map: dict[int, ErrorCode] = {
            401: ErrorCode.AUTHENTICATION_ERROR,
            404: ErrorCode.NOT_FOUND,
            405: ErrorCode.METHOD_NOT_ALLOWED,
        }
        code = code_map.get(exc.status_code, ErrorCode.HTTP_ERROR)
        response = fail_response(exc.status_code, message=message, code=str(code))
        headers = getattr(exc, "headers", None)
        if headers:
            for key, value in headers.items():
                response.headers[key] = value
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
        logger.exception(
            LogEvent.UNHANDLED_EXCEPTION,
            path=request.url.path,
            method=request.method,
            exc_info=exc,
        )
        return fail_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Internal server error",
            code=str(ErrorCode.INTERNAL_ERROR),
        )
