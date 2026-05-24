"""Auth v1 API schemas — request and response models.

Validation rules enforce:
- Password strength (12+ chars, mixed case, digit, special)
- Email format (via email-validator)
- Role whitelist for registration
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, field_validator, model_validator

from app.common.constants import MIN_PASSWORD_LENGTH
from app.common.enums import ClientType, UserRole
from app.common.schemas import BaseSchema, TokenData
from app.common.validators import validate_password_strength

# Cookie name per client type (web clients get refresh token in HttpOnly cookie; driver in body).
COOKIE_NAMES: dict[str, str] = {
    ClientType.ADMIN: "rt_admin",
    ClientType.CUSTOMER_B2B: "rt_customer_b2b",
    ClientType.CUSTOMER_B2C: "rt_customer_b2c",
    ClientType.WAREHOUSE: "rt_warehouse",
    ClientType.DRIVER: "rt_driver",
}


# Registration


# Roles that can self-register (no admin invite required)
_SELF_REGISTER_ROLES: frozenset[str] = frozenset(
    {
        UserRole.CUSTOMER_B2C,
        UserRole.CUSTOMER_B2B,
    }
)


class RegisterRequest(BaseSchema):
    """Customer self-registration request."""

    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=50)
    role: str = Field(default=UserRole.CUSTOMER_B2C)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        return validate_password_strength(v)

    @field_validator("role")
    @classmethod
    def validate_self_register_role(cls, v: str) -> str:
        """Only customers can self-register. Staff roles require admin invite."""
        if v not in _SELF_REGISTER_ROLES:
            raise ValueError(f"Self-registration is only allowed for: {', '.join(sorted(_SELF_REGISTER_ROLES))}")
        return v


class RegisterResponse(BaseSchema):
    """Successful registration response."""

    id: str
    email: str
    first_name: str
    last_name: str
    role: str
    status: str = "INACTIVE"


# Login


class LoginRequest(BaseSchema):
    """Email + password login request."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class LoginServiceResponse(BaseSchema):
    """Service-layer login result (internal). Routes shape into AuthResponse."""

    user: UserBrief
    tokens: TokenData


# Password Reset


class PasswordResetRequest(BaseSchema):
    email: EmailStr


class InviteLinkReminderRequest(BaseSchema):
    email: EmailStr


class PasswordResetVerifyOtpRequest(BaseSchema):
    email: EmailStr
    otp: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")

    @field_validator("otp", mode="before")
    @classmethod
    def _strip_otp(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            return ""
        return s


class PasswordResetSessionResponse(BaseSchema):
    password_reset_token: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[a-f0-9]{64}$",
        description="Send as the `X-Password-Reset-Token` header on POST /auth/confirm-password-reset within its validity window.",
    )
    expires_in: int = Field(ge=1, description="Seconds until the session token expires.")
    expires_at: datetime = Field(description="UTC expiry for the session token.")
    message: str


class PasswordResetConfirm(BaseSchema):
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        return validate_password_strength(v)


# Invites


class InviteValidateResponse(BaseSchema):
    email: str
    first_name: str
    last_name: str
    full_name: str
    role: str


class InviteActivateRequest(BaseSchema):
    """Final activation: set password (invite or driver activation; token in ``X-Invite-Token``)."""

    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        return validate_password_strength(v)


class DriverActivationValidateResponse(BaseSchema):
    """Token classification for driver mobile (deep link flow)."""

    valid: bool
    reason: str | None = Field(
        default=None,
        description="When valid is false: INVALID, EXPIRED, or ALREADY_ACTIVATED.",
    )
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    expires_at: str | None = Field(default=None, description="ISO-8601 expiry when valid is true.")


class DriverActivationResendRequest(BaseSchema):
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_resend_email(cls, v: str) -> str:
        if v is None:
            return ""
        return str(v).strip().lower()


class SupportIssuePasswordRequest(BaseSchema):
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        return validate_password_strength(v)


class SupportIssuePasswordResponse(BaseSchema):
    user_id: str
    email: str


class ChangePasswordRequest(BaseSchema):
    """Authenticated user changes their own password."""

    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        return validate_password_strength(v)

    @model_validator(mode="after")
    def new_password_different_from_current(self) -> ChangePasswordRequest:
        """Reject setting new password to the same as current (rotation compliance)."""
        if self.current_password and self.new_password and self.current_password == self.new_password:
            raise ValueError("New password must be different from current password")
        return self


# Shared


class DriverMeProfile(BaseSchema):
    """Driver-only context for /me. Identity fields live on UserBrief."""

    id: str
    driver_code: str
    terms_accepted_at: datetime | None = None
    location_consent_at: datetime | None = None


class OrgContactMeProfile(BaseSchema):
    """B2B contact-only context for /me. Identity fields live on UserBrief."""

    id: str
    contact_role: str
    status: str
    is_primary: bool


class AdminMeProfile(BaseSchema):
    """Admin-only context for /me. Identity fields live on UserBrief."""

    id: str
    admin_ref: str
    title: str | None = None
    position_role: str | None = None
    address_line_1: str
    address_line_2: str | None = None
    city: str
    state: str
    postcode: str
    country: str


class UserBrief(BaseSchema):
    """Minimal user info returned with auth responses."""

    id: str
    email: str
    first_name: str
    last_name: str
    role: str
    organization_id: str | None = None
    phone: str | None = None
    avatar_url: str | None = None
    contact_role: str | None = None
    region_id: str | None = None
    # When true, the client should redirect the user to set a new password
    # after first login (driver onboarding flow).
    requires_password_change: bool = Field(default=False)
    profile_type: str | None = None
    driver: DriverMeProfile | None = None
    org_contact: OrgContactMeProfile | None = None
    admin: AdminMeProfile | None = None
    created_at: datetime


class SessionDevice(BaseSchema):
    session_id: str
    device_label: str = Field(
        description="Single line for UI, e.g. 'Chrome on Windows' or 'Safari on iPhone' (parsed from user_agent).",
    )
    browser_family: str | None = Field(default=None, description="Raw parser output for browser family.")
    os_family: str | None = Field(default=None, description="Raw parser output for OS family.")
    device_family: str | None = Field(
        default=None,
        description="Device model/family when known (e.g. iPhone); omit for generic desktop.",
    )
    is_mobile: bool = Field(default=False)
    is_tablet: bool = Field(default=False)
    is_pc: bool = Field(default=False)
    user_agent: str | None = Field(
        default=None,
        description="Original User-Agent string (for support); prefer device_label in UI.",
    )
    ip_address: str | None = Field(
        default=None,
        description="Masked client IP (privacy); use location_label when GeoIP is configured.",
    )
    location_label: str | None = Field(
        default=None,
        description="Approximate location from GeoIP when GEOIP_MAXMIND_CITY_DB_PATH is set, e.g. 'Bristol, UK'.",
    )
    last_seen_at: datetime | None = None
    inactivity_expires_at: datetime | None = None
    current: bool = Field(
        default=False,
        description="True when this row matches the caller's access token session (sid). "
        "Show as 'This device' in the UI when true. False for legacy tokens without sid.",
    )


class ActiveSessionsResponse(BaseSchema):
    items: list[SessionDevice]


class LogoutSessionRequest(BaseSchema):
    session_id: UUID
