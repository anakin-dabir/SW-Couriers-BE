"""Step-up authentication service for document access (OTP + token).

Handles:
- Rate-limited OTP generation and email dispatch (per scope: org vs driver docs)
- OTP verification (SlowAPI + Redis lockout after repeated failures per user+scope) and token issuance
- Token validation (DocAccessDep on org document routes; DriverDocAccessDep on driver compliance document routes)
"""

from __future__ import annotations

import random
import secrets
import string
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import SessionDep
from app.common.enums.jobs import Job
from app.common.exceptions import AuthenticationError, RateLimitError, ValidationError
from app.core.config import settings
from app.core.queue import QueuePriority, enqueue
from app.core.redis import get_redis
from app.modules.organizations.doc_access_scope import DocAccessScope
from app.modules.organizations.repository import DocAccessTokenRepository, DocOtpRepository, ShareAccessTokenRepository, ShareOtpRepository

# ── Constants ─────────────────────────────────────────────────────────────────

OTP_TTL_MINUTES = 10
TOKEN_TTL_SECONDS = 3600  # 1 hour
RATE_LIMIT_MAX = 3  # max OTP sends per window
RATE_LIMIT_WINDOW_MINUTES = 10

_DOC_OTP_VERIFY_FAIL_MAX = 5
_DOC_OTP_LOCKOUT_SECONDS = 900
_DOC_OTP_FAIL_WINDOW_SECONDS = 600

logger = structlog.get_logger()


async def _enforce_doc_otp_verify_not_locked(user_id: str, access_scope: DocAccessScope) -> None:
    if settings.is_test:
        return
    redis = get_redis()
    lock_key = f"doc_otp:lock:{user_id}:{access_scope.value}"
    if await redis.get(lock_key):
        ttl = await redis.ttl(lock_key)
        raise RateLimitError(
            "Too many invalid OTP attempts for document access. Please wait before trying again or request a new code.",
            retry_after=max(ttl, 0),
        )


async def _record_doc_otp_verify_failure(user_id: str, access_scope: DocAccessScope) -> None:
    if settings.is_test:
        return
    redis = get_redis()
    fail_key = f"doc_otp:fail:{user_id}:{access_scope.value}"
    lock_key = f"doc_otp:lock:{user_id}:{access_scope.value}"
    n = await redis.incr(fail_key)
    if n == 1:
        await redis.expire(fail_key, _DOC_OTP_FAIL_WINDOW_SECONDS)
    if n >= _DOC_OTP_VERIFY_FAIL_MAX:
        await redis.set(lock_key, "1", ex=_DOC_OTP_LOCKOUT_SECONDS)
        await redis.delete(fail_key)
        logger.warning(
            "doc_access.otp_verify_locked",
            user_id=user_id,
            access_scope=access_scope.value,
            failures=n,
        )


async def _clear_doc_otp_verify_failures(user_id: str, access_scope: DocAccessScope) -> None:
    if settings.is_test:
        return
    redis = get_redis()
    await redis.delete(f"doc_otp:fail:{user_id}:{access_scope.value}")


def _otp_send_paths(scope: DocAccessScope) -> tuple[str, str]:
    if scope is DocAccessScope.DRIVER_DOCUMENTS:
        return (
            "POST /v1/drivers/documents/otp/send",
            "POST /v1/drivers/documents/otp/verify",
        )
    if scope is DocAccessScope.VEHICLE_DOCUMENTS:
        return (
            "POST /v1/vehicles/documents/otp/send",
            "POST /v1/vehicles/documents/otp/verify",
        )
    return (
        "POST /v1/organizations/documents/otp/send",
        "POST /v1/organizations/documents/otp/verify",
    )


def _token_header_name(scope: DocAccessScope) -> str:
    if scope is DocAccessScope.DRIVER_DOCUMENTS:
        return "X-Driver-Doc-Access-Token"
    if scope is DocAccessScope.VEHICLE_DOCUMENTS:
        return "X-Vehicle-Doc-Access-Token"
    return "X-Doc-Access-Token"


class DocAccessService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._otp_repo = DocOtpRepository(session)
        self._token_repo = DocAccessTokenRepository(session)

    # ── Send OTP ──────────────────────────────────────────────────────────────

    async def send_otp(
        self,
        user_id: str,
        user_email: str,
        user_name: str,
        *,
        access_scope: DocAccessScope = DocAccessScope.ORG_DOCUMENTS,
    ) -> None:
        """Generate a 6-digit OTP, persist it, and enqueue the email task.

        Any previous unused, unexpired OTP for the same user and scope is marked used so only
        the newly issued code remains valid.

        Raises ValidationError when the rate limit (3 per 10 min) is exceeded for this scope.
        """
        window_start = datetime.now(UTC) - timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)
        recent = await self._otp_repo.count_recent(user_id, since=window_start, access_scope=access_scope)
        if recent >= RATE_LIMIT_MAX:
            raise ValidationError(
                f"Too many OTP requests. Maximum {RATE_LIMIT_MAX} per "
                f"{RATE_LIMIT_WINDOW_MINUTES} minutes. Please wait and try again."
            )

        await self._otp_repo.invalidate_unused_active_for_user_scope(user_id, access_scope=access_scope)

        otp_code = "".join(random.choices(string.digits, k=6))
        expires_at = datetime.now(UTC) + timedelta(minutes=OTP_TTL_MINUTES)

        await self._otp_repo.create(
            user_id=user_id,
            otp_code=otp_code,
            expires_at=expires_at,
            access_scope=access_scope,
        )
        await self.session.commit()

        await enqueue(
            Job.SEND_DOC_OTP_EMAIL,
            to_email=user_email,
            otp_code=otp_code,
            user_name=user_name,
            expires_in_minutes=OTP_TTL_MINUTES,
            access_scope=access_scope.value,
            priority=QueuePriority.HIGH,
        )

    # ── Verify OTP → issue access token ───────────────────────────────────────

    async def verify_otp(
        self,
        user_id: str,
        otp_code: str,
        *,
        access_scope: DocAccessScope = DocAccessScope.ORG_DOCUMENTS,
    ) -> dict:
        """Validate OTP, mark it used, and return a 1-hour document access token.

        Returns: {"doc_access_token": str, "expires_in": int, "expires_at": datetime}
        Raises AuthenticationError for invalid / expired OTPs.
        Raises RateLimitError when verify is locked out after repeated failures (Redis).
        """
        await _enforce_doc_otp_verify_not_locked(user_id, access_scope)

        otp = await self._otp_repo.find_valid(user_id=user_id, otp_code=otp_code, access_scope=access_scope)
        if otp is None:
            await _record_doc_otp_verify_failure(user_id, access_scope)
            raise AuthenticationError("Invalid or expired OTP. Please request a new one.")

        await _clear_doc_otp_verify_failures(user_id, access_scope)

        await self._otp_repo.mark_used(otp.id)

        raw_token = secrets.token_hex(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=TOKEN_TTL_SECONDS)

        await self._token_repo.create(
            user_id=user_id,
            raw_token=raw_token,
            expires_at=expires_at,
            access_scope=access_scope,
        )
        await self.session.commit()

        return {
            "doc_access_token": raw_token,
            "expires_in": TOKEN_TTL_SECONDS,
            "expires_at": expires_at,
        }

    # ── Validate token (used by DocAccessDep / DriverDocAccessDep) ───────────

    async def validate_token(self, token: str, user_id: str, *, access_scope: DocAccessScope) -> None:
        """Raise AuthenticationError if the doc access token is invalid, mis-owned, or wrong scope."""
        send_path, verify_path = _otp_send_paths(access_scope)
        header = _token_header_name(access_scope)
        row = await self._token_repo.find_valid(token, access_scope=access_scope)
        if row is None:
            raise AuthenticationError(
                f"Document access token is missing, invalid, or expired. "
                f"Request a new OTP via {send_path} "
                f"and verify it via {verify_path}. "
                f"Pass the token as the `{header}` header."
            )
        if row.user_id != user_id:
            raise AuthenticationError("Document access token does not belong to this user.")


# ── FastAPI dependency ─────────────────────────────────────────────────────────


def _get_doc_access_service(session: SessionDep) -> DocAccessService:
    return DocAccessService(session)


DocAccessServiceDep = Annotated[DocAccessService, Depends(_get_doc_access_service)]


# ── Share-link OTP service (unauthenticated external recipients) ───────────────

SHARE_OTP_TTL_MINUTES = 10
SHARE_TOKEN_TTL_SECONDS = 3600  # 1 hour
SHARE_OTP_RATE_LIMIT_MAX = 3
SHARE_OTP_RATE_LIMIT_WINDOW_MINUTES = 10
_SHARE_OTP_VERIFY_FAIL_MAX = 5
_SHARE_OTP_LOCKOUT_SECONDS = 900
_SHARE_OTP_FAIL_WINDOW_SECONDS = 600


def _normalize_share_otp_email(recipient_email: str) -> str:
    return recipient_email.strip().lower()


async def _enforce_share_otp_verify_not_locked(share_token: str, recipient_email: str) -> None:
    if settings.is_test:
        return
    redis = get_redis()
    email = _normalize_share_otp_email(recipient_email)
    lock_key = f"share_otp:lock:{share_token}:{email}"
    if await redis.get(lock_key):
        ttl = await redis.ttl(lock_key)
        raise RateLimitError(
            "Too many invalid OTP attempts. Please wait before trying again or request a new code.",
            retry_after=max(ttl, 0),
        )


async def _record_share_otp_verify_failure(share_token: str, recipient_email: str) -> None:
    if settings.is_test:
        return
    redis = get_redis()
    email = _normalize_share_otp_email(recipient_email)
    fail_key = f"share_otp:fail:{share_token}:{email}"
    lock_key = f"share_otp:lock:{share_token}:{email}"
    n = await redis.incr(fail_key)
    if n == 1:
        await redis.expire(fail_key, _SHARE_OTP_FAIL_WINDOW_SECONDS)
    if n >= _SHARE_OTP_VERIFY_FAIL_MAX:
        await redis.set(lock_key, "1", ex=_SHARE_OTP_LOCKOUT_SECONDS)
        await redis.delete(fail_key)
        logger.warning(
            "share_otp.verify_locked",
            share_token_suffix=share_token[-8:] if len(share_token) >= 8 else share_token,
            recipient_email=email,
            failures=n,
        )


async def _clear_share_otp_verify_failures(share_token: str, recipient_email: str) -> None:
    if settings.is_test:
        return
    redis = get_redis()
    email = _normalize_share_otp_email(recipient_email)
    await redis.delete(f"share_otp:fail:{share_token}:{email}")


class ShareOtpService:
    """OTP flow for unauthenticated external recipients of a document share link.

    Unlike DocAccessService (which is tied to authenticated SW Couriers users),
    this service identifies recipients by email + share_token only — no user account required.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._otp_repo = ShareOtpRepository(session)
        self._token_repo = ShareAccessTokenRepository(session)

    async def send_otp(self, recipient_email: str, share_token: str, document_title: str | None) -> None:
        """Generate a 6-digit OTP and enqueue the share-OTP email.

        Raises ValidationError when the rate limit (3 per 10 min) is exceeded.
        """
        window_start = datetime.now(UTC) - timedelta(minutes=SHARE_OTP_RATE_LIMIT_WINDOW_MINUTES)
        recent = await self._otp_repo.count_recent(recipient_email, share_token, since=window_start)
        if recent >= SHARE_OTP_RATE_LIMIT_MAX:
            raise ValidationError(
                f"Too many OTP requests. Maximum {SHARE_OTP_RATE_LIMIT_MAX} per "
                f"{SHARE_OTP_RATE_LIMIT_WINDOW_MINUTES} minutes. Please wait and try again."
            )

        await self._otp_repo.invalidate_unused_active_for_recipient(recipient_email, share_token)

        otp_code = "".join(secrets.choice(string.digits) for _ in range(6))
        expires_at = datetime.now(UTC) + timedelta(minutes=SHARE_OTP_TTL_MINUTES)

        await self._otp_repo.create(
            recipient_email=recipient_email,
            share_token=share_token,
            otp_code=otp_code,
            expires_at=expires_at,
        )
        await self.session.commit()

        await enqueue(
            Job.SEND_SHARE_OTP_EMAIL,
            to_email=recipient_email,
            otp_code=otp_code,
            document_title=document_title,
            expires_in_minutes=SHARE_OTP_TTL_MINUTES,
            priority=QueuePriority.HIGH,
        )

    async def verify_otp(self, recipient_email: str, share_token: str, otp_code: str) -> dict:
        """Validate OTP, mark it used, and return a 1-hour share access token.

        Returns: {"share_access_token": str, "expires_in": int, "expires_at": datetime}
        Raises AuthenticationError for invalid / expired OTPs.
        Raises RateLimitError when verify is locked out after repeated failures (Redis).
        """
        await _enforce_share_otp_verify_not_locked(share_token, recipient_email)

        otp = await self._otp_repo.find_valid(recipient_email, share_token, otp_code)
        if otp is None:
            await _record_share_otp_verify_failure(share_token, recipient_email)
            raise AuthenticationError("Invalid or expired OTP. Please request a new one.")

        await _clear_share_otp_verify_failures(share_token, recipient_email)

        await self._otp_repo.mark_used(otp.id)

        raw_token = secrets.token_hex(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=SHARE_TOKEN_TTL_SECONDS)

        await self._token_repo.create(
            recipient_email=recipient_email,
            share_token=share_token,
            raw_token=raw_token,
            expires_at=expires_at,
        )
        await self.session.commit()

        return {
            "share_access_token": raw_token,
            "expires_in": SHARE_TOKEN_TTL_SECONDS,
            "expires_at": expires_at,
        }

    async def validate_token(self, token: str, share_token: str) -> str:
        """Validate a share access token and return the recipient_email.

        Raises AuthenticationError if missing, expired, or wrong share.
        """
        row = await self._token_repo.find_valid(token, share_token)
        if row is None:
            raise AuthenticationError(
                "Share access token is missing, invalid, or expired. "
                "Request a new OTP via POST /{share_token}/otp/send."
            )
        return row.recipient_email


def _get_share_otp_service(session: SessionDep) -> ShareOtpService:
    return ShareOtpService(session)


ShareOtpServiceDep = Annotated[ShareOtpService, Depends(_get_share_otp_service)]
