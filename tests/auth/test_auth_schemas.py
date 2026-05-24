"""Schema validation unit tests — no DB required.

Tests Pydantic validators for RegisterRequest, LoginRequest,
ChangePasswordRequest. Covers password strength, role whitelisting,
email format, and field boundary conditions.
"""

import pytest
from pydantic import ValidationError

from app.common.constants import MIN_PASSWORD_LENGTH
from app.modules.auth.v1.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
)

# ── Valid registration payload (reused across tests) ─────

VALID_REGISTER = {
    "email": "user@example.com",
    "password": "StrongPass123!",
    "first_name": "John",
    "last_name": "Doe",
}


# ═══════════════════════════════════════════════════
#  REGISTRATION — Happy Paths
# ═══════════════════════════════════════════════════


class TestRegisterHappyPath:
    """Valid registration payloads should be accepted."""

    def test_valid_b2c_registration(self) -> None:
        """B2C customer (default role) registers successfully."""
        req = RegisterRequest(**VALID_REGISTER)
        assert req.email == "user@example.com"
        assert req.first_name == "John"
        assert req.role == "CUSTOMER_B2C"

    def test_valid_b2b_registration(self) -> None:
        """B2B customer role is allowed for self-registration."""
        req = RegisterRequest(**{**VALID_REGISTER, "role": "CUSTOMER_B2B"})
        assert req.role == "CUSTOMER_B2B"

    def test_password_at_min_length(self) -> None:
        """Password at exactly MIN_PASSWORD_LENGTH should pass."""
        pw = "Aa1!" + "x" * (MIN_PASSWORD_LENGTH - 4)
        assert len(pw) == MIN_PASSWORD_LENGTH
        req = RegisterRequest(**{**VALID_REGISTER, "password": pw})
        assert req.password == pw

    def test_password_at_max_length(self) -> None:
        """Password at exactly 128 characters should pass."""
        pw = "A" * 60 + "a" * 60 + "1234567!"  # 128 chars
        assert len(pw) == 128
        req = RegisterRequest(**{**VALID_REGISTER, "password": pw})
        assert req.password == pw

    def test_optional_phone(self) -> None:
        """Phone field is optional and defaults to None."""
        req = RegisterRequest(**VALID_REGISTER)
        assert req.phone is None

    def test_phone_provided(self) -> None:
        """Phone field can be provided."""
        req = RegisterRequest(**{**VALID_REGISTER, "phone": "+447911123456"})
        assert req.phone == "+447911123456"

    def test_whitespace_stripped(self) -> None:
        """BaseSchema's str_strip_whitespace strips first_name."""
        req = RegisterRequest(**{**VALID_REGISTER, "first_name": "  John  "})
        assert req.first_name == "John"


# ═══════════════════════════════════════════════════
#  REGISTRATION — Role Whitelist (Security-Critical)
# ═══════════════════════════════════════════════════


class TestRegisterRoleWhitelist:
    """Staff roles MUST be rejected for self-registration."""

    @pytest.mark.parametrize(
        "role",
        ["ADMIN", "WAREHOUSE_STAFF", "DRIVER"],
        ids=["admin", "warehouse", "driver"],
    )
    def test_staff_roles_rejected(self, role: str) -> None:
        """Staff roles cannot self-register — admin invite required."""
        with pytest.raises(ValidationError) as exc_info:
            RegisterRequest(**{**VALID_REGISTER, "role": role})
        errors = exc_info.value.errors()
        assert any("role" in str(e["loc"]) for e in errors)

    def test_invalid_role_string_rejected(self) -> None:
        """Random string for role is rejected."""
        with pytest.raises(ValidationError):
            RegisterRequest(**{**VALID_REGISTER, "role": "superuser"})

    def test_empty_role_rejected(self) -> None:
        """Empty string for role is rejected."""
        with pytest.raises(ValidationError):
            RegisterRequest(**{**VALID_REGISTER, "role": ""})


# ═══════════════════════════════════════════════════
#  REGISTRATION — Password Strength
# ═══════════════════════════════════════════════════


class TestRegisterPasswordStrength:
    """Password must meet all complexity requirements."""

    def test_password_too_short(self) -> None:
        """Password under MIN_PASSWORD_LENGTH is rejected."""
        short_pw = "Aa1!" + "x" * max(0, MIN_PASSWORD_LENGTH - 5)
        assert len(short_pw) < MIN_PASSWORD_LENGTH
        with pytest.raises(ValidationError) as exc_info:
            RegisterRequest(**{**VALID_REGISTER, "password": short_pw})
        errors = exc_info.value.errors()
        assert any("password" in str(e["loc"]) for e in errors)

    def test_password_no_uppercase(self) -> None:
        """Password without uppercase letter is rejected."""
        with pytest.raises(ValidationError):
            RegisterRequest(**{**VALID_REGISTER, "password": "alllowercase1!!"})

    def test_password_no_lowercase(self) -> None:
        """Password without lowercase letter is rejected."""
        with pytest.raises(ValidationError):
            RegisterRequest(**{**VALID_REGISTER, "password": "ALLUPPERCASE1!!"})

    def test_password_no_digit(self) -> None:
        """Password without digit is rejected."""
        with pytest.raises(ValidationError):
            RegisterRequest(**{**VALID_REGISTER, "password": "NoDigitsHere!!!"})

    def test_password_no_special_char(self) -> None:
        """Password without special character is rejected."""
        with pytest.raises(ValidationError):
            RegisterRequest(**{**VALID_REGISTER, "password": "NoSpecialChar123"})

    def test_password_exceeds_max_length(self) -> None:
        """Password over 128 characters is rejected."""
        pw = "A" * 60 + "a" * 60 + "12345678!"  # 129 chars
        assert len(pw) == 129
        with pytest.raises(ValidationError):
            RegisterRequest(**{**VALID_REGISTER, "password": pw})


# ═══════════════════════════════════════════════════
#  REGISTRATION — Field Validation
# ═══════════════════════════════════════════════════


class TestRegisterFieldValidation:
    """Field-level validation: email format, name length, etc."""

    def test_invalid_email_format(self) -> None:
        """Non-email string is rejected."""
        with pytest.raises(ValidationError):
            RegisterRequest(**{**VALID_REGISTER, "email": "not-an-email"})

    def test_empty_first_name(self) -> None:
        """Empty first_name is rejected (min_length=1)."""
        with pytest.raises(ValidationError):
            RegisterRequest(**{**VALID_REGISTER, "first_name": ""})

    def test_first_name_too_long(self) -> None:
        """first_name over 100 characters is rejected."""
        with pytest.raises(ValidationError):
            RegisterRequest(**{**VALID_REGISTER, "first_name": "A" * 101})

    def test_empty_last_name(self) -> None:
        """Empty last_name is rejected (min_length=1)."""
        with pytest.raises(ValidationError):
            RegisterRequest(**{**VALID_REGISTER, "last_name": ""})

    def test_missing_email(self) -> None:
        """Missing email field raises ValidationError."""
        data = {**VALID_REGISTER}
        del data["email"]
        with pytest.raises(ValidationError):
            RegisterRequest(**data)

    def test_missing_password(self) -> None:
        """Missing password field raises ValidationError."""
        data = {**VALID_REGISTER}
        del data["password"]
        with pytest.raises(ValidationError):
            RegisterRequest(**data)


# ═══════════════════════════════════════════════════
#  LOGIN — Validation
# ═══════════════════════════════════════════════════


class TestLoginRequestValidation:
    """Login request schema validation."""

    def test_valid_login(self) -> None:
        """Valid email + password accepted."""
        req = LoginRequest(email="user@example.com", password="anything")
        assert req.email == "user@example.com"

    def test_invalid_email(self) -> None:
        """Non-email string is rejected."""
        with pytest.raises(ValidationError):
            LoginRequest(email="notvalid", password="anything")

    def test_empty_password_rejected(self) -> None:
        """Empty password is rejected (min_length=1)."""
        with pytest.raises(ValidationError):
            LoginRequest(email="user@example.com", password="")


# ═══════════════════════════════════════════════════
#  CHANGE PASSWORD — Validation
# ═══════════════════════════════════════════════════


class TestChangePasswordValidation:
    """ChangePasswordRequest schema validation."""

    def test_valid_change_password(self) -> None:
        """Valid current + new password accepted."""
        req = ChangePasswordRequest(
            current_password="OldPassword123!",
            new_password="NewPassword456!",
        )
        assert req.current_password == "OldPassword123!"

    def test_weak_new_password_rejected(self) -> None:
        """New password failing strength check is rejected."""
        with pytest.raises(ValidationError):
            ChangePasswordRequest(
                current_password="anything",
                new_password="weakpassword",
            )

    def test_new_password_too_short(self) -> None:
        """New password under MIN_PASSWORD_LENGTH is rejected."""
        short_pw = "Aa1!" + "x" * max(0, MIN_PASSWORD_LENGTH - 5)
        assert len(short_pw) < MIN_PASSWORD_LENGTH
        with pytest.raises(ValidationError):
            ChangePasswordRequest(
                current_password="Anything1!",
                new_password=short_pw,
            )

    def test_new_password_same_as_current_rejected(self) -> None:
        """New password must differ from current password."""
        with pytest.raises(ValidationError):
            ChangePasswordRequest(
                current_password="SamePassword123!",
                new_password="SamePassword123!",
            )
