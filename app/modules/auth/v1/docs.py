from __future__ import annotations

from app.core.swagger import create_doc_entry, custom_entry, error_401_entry, success_entry

REGISTER = create_doc_entry(
    "Register a new customer account",
    {
        201: success_entry(
            "Customer registered",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "email": "newcustomer@example.com",
                "first_name": "Jane",
                "last_name": "Customer",
                "role": "CUSTOMER_B2C",
                "status": "INACTIVE",
                "message": "Registration successful. Please verify your email.",
            },
            message="Registration successful. Please verify your email.",
        ),
    },
)

LOGIN = create_doc_entry(
    "Login with email and password",
    {
        200: custom_entry(
            "Login successful",
            {
                "success": True,
                "message": "User logged in successfully",
                "data": {
                    "id": "00000000-0000-0000-0000-000000000000",
                    "email": "user@example.com",
                    "first_name": "Jane",
                    "last_name": "Customer",
                    "role": "CUSTOMER_B2C",
                    "organization_id": None,
                    "region_id": None,
                    "requires_password_change": False,
                    "created_at": "2024-01-01T00:00:00Z",
                },
                "tokens": {
                    "access_token": "eyJhbGciOi...",
                    "access_token_expires_in": 900,
                    "refresh_token": "dHlwIjoiSldU... (driver only)",
                    "refresh_token_expires_in": 604800,
                },
            },
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid credentials"),
    },
    description="Requires X-Client-Type. Access token is always in the response body. "
    "Refresh token: DRIVER clients receive it in the response body; "
    "web clients (ADMIN, CUSTOMER_B2B, CUSTOMER_B2C, WAREHOUSE) receive it via HttpOnly cookie at path /api/v1/auth/session. "
    "On 401, the error message is always the same generic string (wrong password, unknown email, suspended, inactive, "
    "or pending activation) so callers cannot infer which case applies.",
)

REFRESH = create_doc_entry(
    "Refresh access + refresh tokens",
    {
        200: custom_entry(
            "Tokens refreshed",
            {
                "success": True,
                "message": "Tokens refreshed successfully",
                "tokens": {
                    "access_token": "eyJhbGciOi...",
                    "access_token_expires_in": 900,
                    "refresh_token": "dHlwIjoiSldU... (driver only)",
                    "refresh_token_expires_in": 604800,
                },
            },
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid refresh token"),
    },
    description="POST /api/v1/auth/session/refresh. Requires X-Client-Type. "
    "Access token is always in the response body. "
    "Refresh token: DRIVER receives it in the body; web clients via HttpOnly cookie. "
    "Web: send refresh token in cookie; DRIVER: send it in Authorization: Bearer.",
)

LOGOUT = create_doc_entry(
    "Logout (single session)",
    {
        200: success_entry("Logged out from current session", message="Successfully logged out"),
        401: error_401_entry("AUTHENTICATION_ERROR", "Refresh token missing or invalid"),
    },
    description="POST /api/v1/auth/session/logout. Authenticated by refresh token only (no access token). "
    "X-Client-Type required. Web: refresh token from HttpOnly cookie (path /api/v1/auth/session); DRIVER: Authorization: Bearer. "
    "The paired access token JTI is blacklisted so it cannot be used after logout.",
)

LOGOUT_ALL = create_doc_entry(
    "Logout from all devices",
    {
        200: success_entry("Logged out from all devices", message="Logged out from 3 session(s)"),
        401: error_401_entry("AUTHENTICATION_ERROR", "Refresh token missing or invalid"),
    },
    description="POST /api/v1/auth/session/logout-all. Authenticated by refresh token only (same as single logout). "
    "Revokes all refresh tokens for the user and blacklists all paired access token JTIs. "
    "X-Client-Type required. Web: cookie; DRIVER: Authorization: Bearer.",
)

CHANGE_PASSWORD = create_doc_entry(
    "Change password (authenticated)",
    {
        200: success_entry(
            "Password changed",
            message="Password changed successfully. Please log in again.",
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Current password is incorrect"),
    },
)

REQUEST_PASSWORD_RESET = create_doc_entry(
    "Request password reset email",
    {
        200: success_entry(
            "Password reset email requested",
            message="If an account exists for this email, you will receive a reset code.",
        ),
    },
)

REQUEST_INVITE_LINK_REMINDER = create_doc_entry(
    "Request a new account activation / invite link (expired link flow)",
    {
        200: success_entry(
            "Request recorded",
            message="If this email has a pending invitation, the relevant team has been notified.",
        ),
    },
    description=(
        "Public endpoint for users whose invite or activation link expired. "
        "Always returns the same success message so addresses and account states cannot be inferred. "
        "When the user row for that email exists and ``status`` is ``PENDING_ACTIVATION``, "
        "in-app notifications are created once per suppression window (Redis) unless a new invite is issued "
        "or the account is activated, so repeated POSTs do not duplicate the same reminder. "
        "Recipients: ADMINS write for staff invites, DRIVERS write for driver invites, "
        "or all assigned account managers for B2B."
    ),
)

VERIFY_PASSWORD_RESET_OTP = create_doc_entry(
    "Verify password reset OTP and get a short-lived session token",
    {
        200: {
            "description": (
                "Returns `password_reset_token` — send it as the `X-Password-Reset-Token` header on "
                "POST /auth/confirm-password-reset with the new password in the body. "
                "The OTP is consumed; use the session token before it expires."
            ),
        },
        401: error_401_entry(
            "AUTHENTICATION_ERROR",
            "Invalid or expired verification code",
        ),
    },
)

CONFIRM_PASSWORD_RESET = create_doc_entry(
    "Set a new password (requires X-Client-Type; must match the flow used for request- and verify-OTP)",
    {
        200: success_entry(
            "Password reset confirmed",
            message="Password has been reset. You can now log in.",
        ),
        401: error_401_entry(
            "AUTHENTICATION_ERROR",
            "Invalid or expired reset session",
        ),
    },
)

VALIDATE_INVITE = create_doc_entry(
    "Validate invite token and return prefill for the signup form. POST with X-Invite-Token from the SPA.",
    {
        200: success_entry(
            "Invite valid",
            data={
                "email": "jane@example.com",
                "first_name": "Jane",
                "last_name": "Smith",
                "full_name": "Jane Smith",
                "role": "ADMIN",
            },
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired invite link"),
    },
    description=(
        "Checks the invite token from the invite link. "
        "On success, returns prefill data (name, email, role); proceed to POST /auth/invites/activate with a password."
    ),
)

ACTIVATE_INVITE = create_doc_entry(
    "Set password and activate account",
    {
        201: success_entry("Account activated", message="Account successfully activated. You can now log in."),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired invite link"),
        409: {"description": "Invite already accepted"},
    },
    description=(
        "Sets the user's password and activates the account using the same invite token as validate. "
        "On success the user status becomes ACTIVE and they can log in immediately."
    ),
)

VERIFY_EMAIL = create_doc_entry(
    "Verify email from link",
    {
        200: success_entry("Email verified", message="Email verified. You can now log in."),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired verification link"),
    },
)

ME = create_doc_entry(
    "Get current user info",
    {
        200: success_entry(
            "Current user info",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "email": "user@example.com",
                "first_name": "Jane",
                "last_name": "Customer",
                "role": "CUSTOMER_B2C",
                "organization_id": None,
                "region_id": None,
                "created_at": "2024-01-01T00:00:00Z",
            },
            message="Current user",
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
    },
    description="Requires Authorization: Bearer (access token) and X-Client-Type.",
)


SESSION = create_doc_entry(
    "List active sessions",
    {
        200: success_entry(
            "Sessions retrieved",
            data={
                "items": [
                    {
                        "session_id": "00000000-0000-0000-0000-000000000000",
                        "device_label": "Chrome on Windows",
                        "browser_family": "Chrome",
                        "os_family": "Windows",
                        "device_family": None,
                        "is_mobile": False,
                        "is_tablet": False,
                        "is_pc": True,
                        "user_agent": "Mozilla/5.0 ...",
                        "ip_address": "10.0.*.*",
                        "location_label": "Bristol, United Kingdom",
                        "last_seen_at": "2024-01-01T00:00:00Z",
                        "inactivity_expires_at": "2024-01-08T00:00:00Z",
                        "current": True,
                    }
                ]
            },
            message="Active sessions",
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or revoked access token"),
    },
    description="GET /api/v1/auth/session. Requires Authorization: Bearer (access token) and X-Client-Type. "
    "Each item includes device_label (parsed UA), masked ip_address, optional location_label when "
    "GEOIP_MAXMIND_CITY_DB_PATH is configured, last_seen_at, and current (true = show as this device).",
)


LOGOUT_OTHER = create_doc_entry(
    "Logout from other sessions",
    {
        200: success_entry("Logged out from other sessions", message="Logged out from 2 other session(s)"),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or revoked access token"),
    },
    description="POST /api/v1/auth/session/logout-other. Requires Authorization: Bearer (access token) and X-Client-Type.",
)


LOGOUT_SESSION = create_doc_entry(
    "Logout from one session",
    {
        200: success_entry("Logged out from session", message="Logged out from session(s) (revoked=1)"),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or revoked access token"),
        422: {
            "description": "Validation error (invalid UUID)",
        },
    },
    description=(
        "POST /api/v1/auth/session/logout-session. Requires Authorization: Bearer (access token) and X-Client-Type.\n\n"
        "Request body: `{ \"session_id\": \"<uuid>\" }`."
    ),
)


DRIVER_ACTIVATION_VALIDATE = create_doc_entry(
    "Validate driver activation token (deep link)",
    {
        200: success_entry(
            "Token classification",
            data={
                "valid": True,
                "reason": None,
                "email": "driver@example.com",
                "first_name": "Alex",
                "last_name": "Rider",
                "full_name": "Alex Rider",
                "expires_at": "2026-05-20T12:00:00+00:00",
            },
        ),
        422: {"description": "Validation error — missing `X-Invite-Token` or token shorter than required minimum length."},
    },
    description=(
        "Public. POST `/api/v1/auth/driver-activation/validate` with header **`X-Invite-Token`** "
        "(same secret as the `token` query param on the driver invite landing URL — read it client-side and send as header only). "
        "Optional empty JSON body. "
        "Returns **200** with `valid: true|false` and `reason` when invalid (`INVALID`, `EXPIRED`, `ALREADY_ACTIVATED`). "
        "Does not consume the token. Invite lifetime is **calendar days** from issuance: "
        "default **7**, configurable via `DRIVER_ACTIVATION_INVITE_EXPIRE_DAYS` (allowed range **1–30**). "
        "Same transport as `POST /api/v1/auth/invites/validate`."
    ),
)


DRIVER_ACTIVATION_SET_PASSWORD = create_doc_entry(
    "Complete driver activation (set password)",
    {
        201: success_entry(
            "Password set",
            message="Password set successfully. You can now log in with the driver app.",
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired activation token"),
        409: {"description": "Token already used"},
        422: {
            "description": "Validation error — weak password, or missing/invalid `X-Invite-Token` header.",
        },
    },
    description=(
        "Public. POST `/api/v1/auth/driver-activation/set-password` with **`X-Invite-Token`** and JSON body **`{ \"password\": \"...\" }`** only "
        "(token not in JSON). "
        "Requires `X-Client-Type: DRIVER` on subsequent login. "
        "Single-use token; sibling activation links are invalidated on success. "
        "Same pattern as `POST /api/v1/auth/invites/activate`."
    ),
)


DRIVER_ACTIVATION_RESEND = create_doc_entry(
    "Resend driver activation email",
    {
        200: success_entry(
            "Resend accepted",
            message="If this email is eligible, a new activation link has been sent.",
        ),
        429: {"description": "Too many resend requests for this email (hourly cap)."},
    },
    description=(
        "Public. POST `/api/v1/auth/driver-activation/resend` with JSON `{ \"email\": \"driver@example.com\" }`. "
        "Same neutral message whether the email exists or not (anti-enumeration). "
        "Only drivers in **PENDING_ACTIVATION** with **email not yet verified** receive a new link."
    ),
)
