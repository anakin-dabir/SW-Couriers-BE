"""Shared utility helpers: request parsing, PII masking, link validation."""

import re
from urllib.parse import urlparse

from starlette.requests import Request

from app.common.enums import ClientType
from app.core.config import settings
from app.core.redis import get_redis

# PII Masking


_EMAIL_RE = re.compile(r"^(.{1,2})(.*)(@.+)$")


def mask_email(email: str | None) -> str:
    """Mask an email for safe logging: 'jane.doe@example.com' -> 'ja***@example.com'."""
    if not email:
        return "***"
    m = _EMAIL_RE.match(email)
    if not m:
        return "***"
    return f"{m.group(1)}***{m.group(3)}"


def mask_phone(phone: str | None) -> str:
    """Mask a phone number for safe logging: '+447911123456' -> '+44***3456'."""
    if not phone:
        return "***"
    digits = phone.lstrip("+")
    if len(digits) <= 4:
        return "***"
    prefix = phone[: len(phone) - len(digits)]
    return f"{prefix}{digits[:2]}***{digits[-4:]}"


def mask_ip_address(ip: str | None) -> str | None:
    """Mask an IP address for privacy."""
    if not ip:
        return None
    ip = ip.strip()
    if not ip:
        return None

    if ":" in ip:
        # Coarse IPv6 masking (avoid leaking full address).
        return f"{ip[:4]}:****"

    parts = ip.split(".")
    if len(parts) != 4:
        return "***"
    return f"{parts[0]}.{parts[1]}.*.*"


# Request Helpers


def get_client_ip(request: Request) -> str | None:
    """Extract client IP, respecting X-Forwarded-For when trusted."""
    if getattr(settings, "TRUST_X_FORWARDED_FOR", False):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def get_bearer_token(request: Request) -> str | None:
    """Extract Bearer token from Authorization header."""
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer ") and auth[7:].strip():
        return auth[7:].strip()
    return None


_FORBIDDEN_LINK_SCHEMES = frozenset(
    {"javascript", "data", "vbscript", "file", "blob", "about"},
)


def _email_link_allowed_app_schemes() -> frozenset[str]:
    raw = (settings.EMAIL_LINK_ALLOWED_APP_SCHEMES or "").strip()
    if not raw:
        return frozenset()
    return frozenset(s.strip().lower() for s in raw.split(",") if s.strip())


def validate_link(link: str) -> str:
    """Return link if its URI scheme is allowlisted for security-sensitive email context.

    Allows https always; http only when APP_ENV is development or test; and schemes listed in
    EMAIL_LINK_ALLOWED_APP_SCHEMES (default swcouriers for driver deep links). Rejects javascript:,
    data:, and other non-web schemes not explicitly permitted.
    """
    if not link or not link.strip():
        raise ValueError("Invalid link")
    normalized = link.strip()
    parsed = urlparse(normalized)
    scheme = (parsed.scheme or "").lower()
    if not scheme:
        raise ValueError("Invalid link scheme")
    if scheme in _FORBIDDEN_LINK_SCHEMES:
        raise ValueError("Invalid link scheme")
    if scheme == "https":
        return normalized
    if scheme == "http":
        if settings.APP_ENV not in ("development", "test"):
            raise ValueError("Invalid link scheme")
        return normalized
    if scheme in _email_link_allowed_app_schemes():
        return normalized
    raise ValueError("Invalid link scheme")


# Link base URLs (per client type, from settings)


def get_link_base_url(client_type: ClientType) -> str:
    """Return the base URL for email links (invite, verify-email) for the given client type.

    Uses LINK_BASE_URL_<CLIENT_TYPE> from settings; falls back to VERIFICATION_LINK_BASE_URL if unset.
    """
    attr = f"LINK_BASE_URL_{client_type.value}"
    base = getattr(settings, attr, "") or settings.VERIFICATION_LINK_BASE_URL
    return (base or "").strip().rstrip("/")


def build_email_link(client_type: ClientType, path: str, token: str) -> str:
    """Build a link for the given client type: base + path + ?token=.

    path: e.g. 'accept-invite', 'verify-email'.
    The token is for the web app landing URL; invite API calls use X-Invite-Token (not query params).
    Returns empty string if no base URL configured for that client type.
    """
    base = get_link_base_url(client_type)
    if not base:
        return ""
    return f"{base}/{path.strip('/')}?token={token}"


def build_driver_set_password_link(*, token: str, email: str) -> str:
    """Universal-link / app-link for driver first-time password (mobile deep link).

    Shape: ``{LINK_BASE_URL_DRIVER}/set-password?email=...&token=...`` (email and token URL-encoded).
    The app reads ``token`` from the link then sends it as ``X-Invite-Token`` on ``POST …/driver-activation/*`` (never as a URL query parameter on the API).
    ``email`` is UX pre-fill for the app only.
    Returns empty string when ``LINK_BASE_URL_DRIVER`` is unset.
    """
    from urllib.parse import urlencode

    base = (settings.LINK_BASE_URL_DRIVER or "").strip()
    if not base and settings.is_test:
        base = "http://localhost"
    if not base:
        return ""
    sep = "" if base.endswith("/") else "/"
    q = urlencode({"email": email.strip().lower(), "token": token.strip()})
    return f"{base}{sep}set-password?{q}"


# Client type (X-Client-Type)


def get_client_type_from_header(value: str | None) -> ClientType:
    """Parse X-Client-Type header into ClientType.

    Raises:
        AuthenticationError: if header is missing or not one of the allowed client types.
    """
    from app.common.exceptions import AuthenticationError

    if not value or not value.strip():
        raise AuthenticationError("Missing Client Type")
    raw = value.strip().upper()
    try:
        return ClientType(raw)
    except ValueError:
        raise AuthenticationError("Invalid Client Type") from None


def verify_client_type_for_role(client_type: ClientType, user_role: str) -> None:
    """Ensure the given X-Client-Type is allowed for the user's role.

    Raises:
        AuthenticationError: if the user's role is not allowed to use this client type.
    """
    from app.common.enums import ROLE_TO_CLIENT_TYPE
    from app.common.exceptions import AuthenticationError

    allowed = ROLE_TO_CLIENT_TYPE.get(user_role)
    if allowed is None or client_type != allowed:
        raise AuthenticationError("Client type not allowed for your role. Use the correct app or portal for your account.")


# Token blacklist (Redis)

_BLACKLIST_PREFIX = "token_bl:"
_SESSION_REVOKED_PREFIX = "session_rev:"


async def is_token_blacklisted(jti: str) -> bool:
    return await get_redis().exists(f"{_BLACKLIST_PREFIX}{jti}") > 0


async def blacklist_token(jti: str, ttl_seconds: int) -> None:
    await get_redis().set(f"{_BLACKLIST_PREFIX}{jti}", "1", ex=ttl_seconds)


# Session revocation (Redis)


async def is_session_revoked(session_id: str) -> bool:
    """Return True if the logical session is revoked (Redis marker).

    This is used for per-request enforcement when the access token carries `sid`.
    """
    return await get_redis().exists(f"{_SESSION_REVOKED_PREFIX}{session_id}") > 0


async def mark_session_revoked(session_id: str, ttl_seconds: int) -> None:
    """Mark a logical session as revoked in Redis with bounded TTL."""
    await get_redis().set(f"{_SESSION_REVOKED_PREFIX}{session_id}", "1", ex=ttl_seconds)


# User suspension (Redis)

_USER_SUSPENDED_PREFIX = "user_suspended:"


async def is_user_suspended(user_id: str) -> bool:
    """Return True when the user is globally suspended.

    Checked by the auth dependency and the refresh-token flow so every
    outstanding access/refresh token is rejected the moment the suspend
    marker is written.
    """
    return await get_redis().exists(f"{_USER_SUSPENDED_PREFIX}{user_id}") > 0


async def mark_user_suspended(user_id: str, ttl_seconds: int) -> None:
    """Mark a user as suspended.

    TTL should cover the longest token lifetime in use (refresh token TTL)
    so the marker outlives any still-valid credential. The DB ``users.status``
    update remains the source of truth; this marker is the fast-path gate.
    """
    await get_redis().set(f"{_USER_SUSPENDED_PREFIX}{user_id}", "1", ex=ttl_seconds)


async def unmark_user_suspended(user_id: str) -> None:
    """Clear the suspended marker (called by reactivate flows)."""
    await get_redis().delete(f"{_USER_SUSPENDED_PREFIX}{user_id}")
