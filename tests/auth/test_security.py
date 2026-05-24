"""T2: Security utility unit tests — no DB required.

Tests Argon2id password hashing, JWT access/refresh token
creation and decoding, token type cross-validation,
expiration, tampering, and SHA-256 token hashing.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app.common.constants import MIN_PASSWORD_LENGTH
from app.common.validators import validate_password_strength
from app.core.config import settings
from app.core.security import (
    TokenType,
    check_needs_rehash,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_secure_password,
    hash_password,
    hash_token,
    verify_password,
)

# ═══════════════════════════════════════════════════
#  PASSWORD HASHING (Argon2id)
# ═══════════════════════════════════════════════════


class TestPasswordHashing:
    """Argon2id password hashing and verification."""

    def test_hash_returns_argon2id_string(self) -> None:
        """Hash output starts with the Argon2id identifier."""
        h = hash_password("MyPassword123!")
        assert h.startswith("$argon2id$")

    def test_verify_correct_password(self) -> None:
        """Correct password verifies successfully."""
        h = hash_password("CorrectPassword!")
        assert verify_password("CorrectPassword!", h) is True

    def test_verify_wrong_password(self) -> None:
        """Wrong password returns False (not an exception)."""
        h = hash_password("CorrectPassword!")
        assert verify_password("WrongPassword!", h) is False

    def test_different_hashes_for_same_password(self) -> None:
        """Same password produces different hashes (random salt).

        Security-critical: if two users choose the same password,
        their hashes must NOT be identical.
        """
        h1 = hash_password("SamePassword123!")
        h2 = hash_password("SamePassword123!")
        assert h1 != h2

    def test_check_needs_rehash_false_for_current_params(self) -> None:
        """Current Argon2 parameters should not trigger rehash."""
        h = hash_password("TestPassword!")
        assert check_needs_rehash(h) is False

    def test_empty_password_hashes(self) -> None:
        """Even empty strings can be hashed (validation is elsewhere)."""
        h = hash_password("")
        assert h.startswith("$argon2id$")
        assert verify_password("", h) is True

    def test_unicode_password(self) -> None:
        """Unicode characters in passwords are handled correctly."""
        pw = "PäsSwörd123!🔐"
        h = hash_password(pw)
        assert verify_password(pw, h) is True
        assert verify_password("PäsSwörd123!🔑", h) is False

    def test_hash_and_verify_roundtrip_with_generated_password(self) -> None:
        """Generated passwords hash correctly and verify (integration with create_user flow)."""
        pw = generate_secure_password()
        h = hash_password(pw)
        assert h.startswith("$argon2id$")
        assert verify_password(pw, h) is True
        assert verify_password(pw + "x", h) is False


# ═══════════════════════════════════════════════════
#  GENERATE SECURE PASSWORD (driver onboarding)
# ═══════════════════════════════════════════════════


class TestGenerateSecurePassword:
    """Auto-generated passwords for driver credentials email must meet app policy."""

    def test_generated_password_meets_min_length(self) -> None:
        """Generated password is at least MIN_PASSWORD_LENGTH."""
        for _ in range(20):
            pw = generate_secure_password()
            assert len(pw) >= MIN_PASSWORD_LENGTH

    def test_generated_password_meets_strength_policy(self) -> None:
        """Generated password passes validate_password_strength (upper, lower, digit, special)."""
        for _ in range(30):
            pw = generate_secure_password()
            assert validate_password_strength(pw) == pw

    def test_generated_password_custom_length(self) -> None:
        """Explicit length is respected (still includes required character classes)."""
        pw = generate_secure_password(length=20)
        assert len(pw) == 20
        assert validate_password_strength(pw) == pw

    def test_generated_passwords_are_different(self) -> None:
        """Each call produces a different password (CSPRNG)."""
        seen = {generate_secure_password() for _ in range(50)}
        assert len(seen) == 50


# ═══════════════════════════════════════════════════
#  JWT ACCESS TOKENS
# ═══════════════════════════════════════════════════


class TestAccessTokens:
    """JWT access token creation and decoding."""

    def test_create_access_token_contains_correct_claims(self) -> None:
        """Access token payload contains all required claims."""
        user_id = str(uuid4())
        token, _ = create_access_token(user_id=user_id, role="CUSTOMER_B2C", client_type="customer")
        payload = decode_token(token, TokenType.ACCESS)

        assert payload["sub"] == user_id
        assert payload["role"] == "CUSTOMER_B2C"
        assert payload["type"] == "ACCESS"
        assert payload["aud"] == "CUSTOMER"
        assert "jti" in payload
        assert "exp" in payload
        assert "iat" in payload

    def test_access_token_includes_region_id(self) -> None:
        """region_id is included when provided."""
        region_id = str(uuid4())
        token, _ = create_access_token(
            user_id=str(uuid4()),
            role="ADMIN",
            client_type="admin",
            region_id=region_id,
        )
        payload = decode_token(token, TokenType.ACCESS)
        assert payload.get("region_id") == region_id

    def test_access_token_includes_org_id(self) -> None:
        """org_id is included when provided."""
        org_id = str(uuid4())
        token, _ = create_access_token(
            user_id=str(uuid4()),
            role="CUSTOMER_B2B",
            client_type="customer",
            organization_id=org_id,
        )
        payload = decode_token(token, TokenType.ACCESS)
        assert payload.get("org_id") == org_id

    def test_access_token_omits_region_and_org_when_none(self) -> None:
        """region_id and org_id are NOT in payload when None."""
        token, _ = create_access_token(user_id=str(uuid4()), role="CUSTOMER_B2C", client_type="customer")
        payload = decode_token(token, TokenType.ACCESS)
        assert "region_id" not in payload
        assert "org_id" not in payload

    def test_expired_access_token_raises(self) -> None:
        """Expired access token raises ExpiredSignatureError on decode."""
        user_id = str(uuid4())
        payload = {
            "sub": user_id,
            "role": "CUSTOMER_B2C",
            "aud": "CUSTOMER",
            "exp": datetime.now(UTC) - timedelta(minutes=1),
            "iat": datetime.now(UTC) - timedelta(minutes=16),
            "jti": str(uuid4()),
            "type": "access",
        }
        token = jwt.encode(
            payload,
            settings.JWT_SECRET_KEY.get_secret_value(),
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(token, TokenType.ACCESS)

    def test_tampered_access_token_raises(self) -> None:
        """Modifying the token payload invalidates the signature."""
        token, _ = create_access_token(user_id=str(uuid4()), role="CUSTOMER_B2C", client_type="customer")
        parts = token.split(".")
        tampered_payload = parts[1][:5] + "X" + parts[1][6:]
        tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"
        with pytest.raises(jwt.InvalidTokenError):
            decode_token(tampered_token, TokenType.ACCESS)

    def test_wrong_secret_key_raises(self) -> None:
        """Token signed with a different key cannot be decoded."""
        payload = {
            "sub": str(uuid4()),
            "role": "CUSTOMER_B2C",
            "aud": "CUSTOMER",
            "exp": datetime.now(UTC) + timedelta(minutes=15),
            "iat": datetime.now(UTC),
            "jti": str(uuid4()),
            "type": "access",
        }
        token = jwt.encode(payload, "wrong-secret-key-completely-different", algorithm="HS256")
        with pytest.raises(jwt.InvalidTokenError):
            decode_token(token, TokenType.ACCESS)

    def test_each_token_has_unique_jti(self) -> None:
        """Every token gets a unique JWT ID (jti) — no reuse."""
        user_id = str(uuid4())
        t1, _ = create_access_token(user_id=user_id, role="CUSTOMER_B2C", client_type="customer")
        t2, _ = create_access_token(user_id=user_id, role="CUSTOMER_B2C", client_type="customer")
        p1 = decode_token(t1, TokenType.ACCESS)
        p2 = decode_token(t2, TokenType.ACCESS)
        assert p1["jti"] != p2["jti"]


# ═══════════════════════════════════════════════════
#  JWT REFRESH TOKENS
# ═══════════════════════════════════════════════════


class TestRefreshTokens:
    """JWT refresh token creation and decoding."""

    def test_create_refresh_token_returns_tuple(self) -> None:
        """Returns (raw_token, hash, expires_at) tuple."""
        raw, token_hash, expires_at = create_refresh_token(str(uuid4()), client_type="customer")
        assert isinstance(raw, str)
        assert isinstance(token_hash, str)
        assert isinstance(expires_at, datetime)

    def test_refresh_token_hash_is_sha256(self) -> None:
        """Token hash is a 64-character hex string (SHA-256)."""
        _, token_hash, _ = create_refresh_token(str(uuid4()), client_type="admin")
        assert len(token_hash) == 64
        assert all(c in "0123456789abcdef" for c in token_hash)

    def test_refresh_token_expires_in_future(self) -> None:
        """Refresh token expires 7 days from now (default)."""
        _, _, expires_at = create_refresh_token(str(uuid4()), client_type="customer")
        now = datetime.now(UTC)
        delta = expires_at - now
        assert delta.total_seconds() > 6 * 86400
        assert delta.total_seconds() <= 7 * 86400

    def test_decode_refresh_token_success(self) -> None:
        """Valid refresh token decodes with correct claims."""
        user_id = str(uuid4())
        raw, _, _ = create_refresh_token(user_id, client_type="driver")
        payload = decode_token(raw, TokenType.REFRESH)
        assert payload["sub"] == user_id
        assert payload["type"] == "REFRESH"
        assert payload["aud"] == "DRIVER"


# ═══════════════════════════════════════════════════
#  CROSS-TOKEN-TYPE ATTACKS (Security-Critical)
# ═══════════════════════════════════════════════════


class TestCrossTokenTypeAttacks:
    """Refresh tokens must NOT work as access tokens and vice versa.

    This prevents token confusion attacks where an attacker presents
    a refresh token to an endpoint expecting an access token.
    """

    def test_refresh_token_rejected_as_access_token(self) -> None:
        """A refresh token cannot be decoded as an access token."""
        raw, _, _ = create_refresh_token(str(uuid4()), client_type="customer")
        with pytest.raises(jwt.InvalidTokenError):
            decode_token(raw, TokenType.ACCESS)

    def test_access_token_rejected_as_refresh_token(self) -> None:
        """An access token cannot be decoded as a refresh token."""
        token, _ = create_access_token(user_id=str(uuid4()), role="CUSTOMER_B2C", client_type="customer")
        with pytest.raises(jwt.InvalidTokenError):
            decode_token(token, TokenType.REFRESH)


# ═══════════════════════════════════════════════════
#  TOKEN HASHING (SHA-256)
# ═══════════════════════════════════════════════════


class TestTokenHashing:
    """SHA-256 token hashing for DB storage."""

    def test_hash_is_deterministic(self) -> None:
        """Same input always produces the same hash."""
        assert hash_token("my-token") == hash_token("my-token")

    def test_different_inputs_different_hashes(self) -> None:
        """Different tokens produce different hashes."""
        assert hash_token("token-1") != hash_token("token-2")

    def test_hash_is_64_hex_chars(self) -> None:
        """SHA-256 output is 64 hexadecimal characters."""
        h = hash_token("any-input")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)
