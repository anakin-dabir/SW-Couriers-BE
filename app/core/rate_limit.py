from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.common.enums import ErrorCode
from app.common.response import error_response
from app.core.config import settings

DEFAULT_RATE_LIMIT = settings.RATE_LIMIT_DEFAULT
AUTH_RATE_LIMIT = settings.RATE_LIMIT_AUTH
DRIVERS_WRITE_RATE_LIMIT = settings.RATE_LIMIT_DRIVERS_WRITE
DRIVERS_READ_RATE_LIMIT = settings.RATE_LIMIT_DRIVERS_READ
SUSPENSION_RULES_WRITE_RATE_LIMIT = settings.RATE_LIMIT_SUSPENSION_RULES_WRITE
SUSPENSION_RULES_READ_RATE_LIMIT = settings.RATE_LIMIT_SUSPENSION_RULES_READ
SERVICE_TIER_WRITE_RATE_LIMIT = settings.RATE_LIMIT_SERVICE_TIER_WRITE
SERVICE_TIER_READ_RATE_LIMIT = settings.RATE_LIMIT_SERVICE_TIER_READ
SHARED_DOC_PASSWORD_RATE_LIMIT = settings.RATE_LIMIT_SHARED_DOC_PASSWORD
DOC_OTP_VERIFY_RATE_LIMIT = settings.RATE_LIMIT_DOC_OTP_VERIFY
SHARED_DOC_OTP_RATE_LIMIT = settings.RATE_LIMIT_SHARED_DOC_OTP
QUICKBOOKS_SYNC_RATE_LIMIT = settings.RATE_LIMIT_QUICKBOOKS_SYNC
QUICKBOOKS_RESYNC_RATE_LIMIT = settings.RATE_LIMIT_QUICKBOOKS_RESYNC
QUICKBOOKS_RECONCILE_RATE_LIMIT = settings.RATE_LIMIT_QUICKBOOKS_RECONCILE
QUICKBOOKS_CALLBACK_RATE_LIMIT = settings.RATE_LIMIT_QUICKBOOKS_CALLBACK
QUICKBOOKS_CALLBACK_DUPLICATE_RATE_LIMIT = settings.RATE_LIMIT_QUICKBOOKS_CALLBACK_DUPLICATE


def _key_func(request: Request) -> str:
    if getattr(settings, "TRUST_X_FORWARDED_FOR", False):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(
    key_func=_key_func,
    default_limits=[DEFAULT_RATE_LIMIT],
    storage_uri=settings.REDIS_URL,
    enabled=not settings.is_test,
    headers_enabled=True,
    in_memory_fallback_enabled=True,
)


async def rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    retry_after = getattr(exc, "retry_after", None)
    response = error_response(
        429,
        "Too many requests. Please try again later.",
        str(ErrorCode.RATE_LIMIT_EXCEEDED),
    )
    if retry_after:
        response.headers["Retry-After"] = str(retry_after)
    return response
