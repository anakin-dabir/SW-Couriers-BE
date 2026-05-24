"""Security utilities: JWT token management and password hashing.

- JWT: PyJWT with HS256, access tokens (15min) + refresh tokens (7 days)
- Password hashing: Argon2id via argon2-cffi (OWASP recommendation)
- Token blacklist check via Redis JTI blacklist
"""

import hashlib
import secrets
import string
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from app.common.constants import MIN_PASSWORD_LENGTH
from app.core.config import settings

# Password Hashing (Argon2id)

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError:
        return False


def check_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def generate_secure_password(length: int | None = None) -> str:
    """Generate a cryptographically secure password meeting app policy.

    Ensures at least one uppercase, one lowercase, one digit, one special char,
    and total length >= MIN_PASSWORD_LENGTH. Uses secrets for CSPRNG.
    """
    length = length or max(MIN_PASSWORD_LENGTH, 16)
    uppercase = string.ascii_uppercase
    lowercase = string.ascii_lowercase
    digits = string.digits
    special = "!@#$%&*+-=?"
    required = [
        secrets.choice(uppercase),
        secrets.choice(lowercase),
        secrets.choice(digits),
        secrets.choice(special),
    ]
    alphabet = uppercase + lowercase + digits + special
    rest = [secrets.choice(alphabet) for _ in range(length - len(required))]
    chars = required + rest
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


# Token Types & Decoding


class TokenType(StrEnum):
    ACCESS = "ACCESS"
    REFRESH = "REFRESH"
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION"


_TOKEN_SECRETS: dict[TokenType, Callable[[], str]] = {
    TokenType.ACCESS: lambda: settings.JWT_SECRET_KEY.get_secret_value(),
    TokenType.REFRESH: lambda: settings.JWT_REFRESH_SECRET_KEY.get_secret_value(),
    TokenType.EMAIL_VERIFICATION: lambda: settings.email_verification_secret,
}

EMAIL_VERIFICATION_EXPIRE_HOURS = 24
PASSWORD_RESET_EXPIRE_MINUTES = 15
PASSWORD_RESET_OTP_LENGTH = 6
PASSWORD_RESET_SESSION_MINUTES = 30

_TOKEN_EXPIRY: dict[TokenType, Callable[[], timedelta]] = {
    TokenType.ACCESS: lambda: timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
    TokenType.REFRESH: lambda: timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
    TokenType.EMAIL_VERIFICATION: lambda: timedelta(hours=EMAIL_VERIFICATION_EXPIRE_HOURS),
}


def _normalize_aud(client_type: str | Any) -> str:
    """Uppercase string for JWT aud claim (ClientType or str)."""
    if isinstance(client_type, str):
        return client_type.strip().upper()
    return getattr(client_type, "value", str(client_type)).strip().upper()


def create_token(token_type: TokenType, sub: str, **claims: Any) -> tuple[str, datetime, str]:
    """Create any JWT token type. Returns (encoded_token, expires_at, jti).

    Required claims by type:
      ACCESS: role, aud (client_type). Optional: region_id, org_id.
      REFRESH: aud (client_type).
      EMAIL_VERIFICATION: none.
    """
    now = datetime.now(UTC)
    delta = _TOKEN_EXPIRY[token_type]()
    expires_at = now + delta
    jti = str(uuid4())

    payload: dict[str, Any] = {
        "sub": sub,
        "iat": now,
        "exp": expires_at,
        "jti": jti,
        "type": token_type,
    }

    if token_type == TokenType.ACCESS or token_type == TokenType.REFRESH:
        payload["role"] = claims.pop("role", "")
        payload["aud"] = _normalize_aud(claims.pop("aud", ""))
        if claims.get("region_id"):
            payload["region_id"] = claims.pop("region_id")
        if claims.get("org_id"):
            payload["org_id"] = claims.pop("org_id")

        # Any remaining non-null claims are included as-is. This enables optional
        # additive claims like sid/sv while keeping backward compatibility.
        for k, v in list(claims.items()):
            if v is not None:
                payload[k] = v
            # remove so we don't accidentally reuse claims later
            claims.pop(k, None)

    token = jwt.encode(
        payload,
        _TOKEN_SECRETS[token_type](),
        algorithm=settings.JWT_ALGORITHM,
    )
    return token, expires_at, jti


def decode_token(token: str, expected: TokenType) -> dict[str, Any]:
    """Decode any JWT token type. Raises jwt.PyJWTError on failure."""
    payload = jwt.decode(
        token,
        _TOKEN_SECRETS[expected](),
        algorithms=[settings.JWT_ALGORITHM],
        options={"verify_aud": False},
    )
    if payload.get("type") != expected:
        raise jwt.InvalidTokenError(f"Expected {expected} token")
    return payload


# ── JWT Token Creation ───────────────────────


def create_access_token(
    user_id: str,
    role: str,
    client_type: str | Any,
    region_id: str | None = None,
    organization_id: str | None = None,
    *,
    sid: str | None = None,
    sv: int | None = None,
) -> tuple[str, str]:
    """Short-lived JWT access token. Returns (encoded_token, jti)."""
    token, _, jti = create_token(
        TokenType.ACCESS,
        sub=user_id,
        role=role,
        aud=client_type,
        region_id=region_id or None,
        org_id=organization_id,
        sid=sid,
        sv=sv,
    )
    return token, jti


def create_refresh_token(user_id: str, client_type: str | Any) -> tuple[str, str, datetime]:
    """Refresh token. Returns (raw_token, token_hash, expires_at) for DB storage."""
    token, expires_at, _ = create_token(TokenType.REFRESH, sub=user_id, aud=client_type)
    return token, hash_token(token), expires_at


def create_email_verification_token(user_id: str) -> str:
    """Short-lived JWT for email verification (24h)."""
    token, _, _ = create_token(TokenType.EMAIL_VERIFICATION, sub=user_id)
    return token


def hash_token(token: str) -> str:
    """SHA-256 hash of a token for secure DB storage."""
    return hashlib.sha256(token.encode()).hexdigest()
