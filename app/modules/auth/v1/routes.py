from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, Request, Response, status

from app.common.deps import ClientTypeDep, CurrentUserDep
from app.common.enums import ClientType
from app.common.exceptions import AuthenticationError
from app.common.response import ok
from app.common.schemas import AuthResponse, MessageResponse, SuccessResponse, TokenData
from app.common.utils import get_bearer_token
from app.core.config import settings
from app.core.rate_limit import AUTH_RATE_LIMIT, limiter
from app.modules.auth.cookies import clear_refresh_token_cookie, set_refresh_token_cookie
from app.modules.auth.invite_header import InviteTokenDep
from app.modules.auth.service import AuthService
from app.modules.auth.v1.docs import (
    ACTIVATE_INVITE,
    CHANGE_PASSWORD,
    CONFIRM_PASSWORD_RESET,
    LOGIN,
    LOGOUT,
    LOGOUT_ALL,
    LOGOUT_OTHER,
    LOGOUT_SESSION,
    ME,
    REFRESH,
    REGISTER,
    REQUEST_INVITE_LINK_REMINDER,
    REQUEST_PASSWORD_RESET,
    SESSION,
    VALIDATE_INVITE,
    VERIFY_EMAIL,
    VERIFY_PASSWORD_RESET_OTP,
)
from app.modules.auth.v1.schemas import (
    COOKIE_NAMES,
    ActiveSessionsResponse,
    ChangePasswordRequest,
    InviteActivateRequest,
    InviteValidateResponse,
    InviteLinkReminderRequest,
    LoginRequest,
    LogoutSessionRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetSessionResponse,
    PasswordResetVerifyOtpRequest,
    RegisterRequest,
    RegisterResponse,
    UserBrief,
    SessionDevice,
)

logger = structlog.get_logger()

router = APIRouter()
session_router = APIRouter(prefix="/session")


AuthServiceDep = Annotated[AuthService, Depends(AuthService.dep)]

# Registration


@router.post(
    "/register",
    response_model=SuccessResponse[RegisterResponse],
    status_code=status.HTTP_201_CREATED,
    **REGISTER,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def register(
    request: Request,
    data: RegisterRequest,
    response: Response,
    auth_service: AuthServiceDep,
) -> dict:
    result = await auth_service.register(data)
    return ok(result, message="Registration successful. Please verify your email.")


# Login


@router.post(
    "/login",
    response_model=AuthResponse[UserBrief],
    **LOGIN,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def login(
    request: Request,
    data: LoginRequest,
    response: Response,
    auth_service: AuthServiceDep,
    client_type: ClientTypeDep,
) -> dict:
    result = await auth_service.login(
        email=data.email,
        password=data.password,
        client_type=client_type,
    )
    logger.info("User logged in", email=data.email, client_type=client_type.value, user_id=result.user.id)
    tokens = TokenData(
        access_token=result.tokens.access_token,
        access_token_expires_in=result.tokens.access_token_expires_in,
    )
    if client_type == ClientType.DRIVER:
        tokens.refresh_token = result.tokens.refresh_token
        tokens.refresh_token_expires_in = result.tokens.refresh_token_expires_in
    elif result.tokens.refresh_token:
        set_refresh_token_cookie(response, client_type, result.tokens.refresh_token)
        # #region agent log
        _ua = (request.headers.get("user-agent") or "")[:120]
        logger.info(
            "agent_debug",
            session_id="89e7eb",
            hypothesis_id="D",
            location="routes.py:login",
            message="refresh cookie set on login",
            client_type=client_type.value,
            origin=request.headers.get("origin"),
            is_safari="Safari" in _ua and "Chrome" not in _ua,
            cookie_samesite=getattr(settings, "COOKIE_SAMESITE", None),
            cookie_partitioned=getattr(settings, "COOKIE_SAMESITE", "none").lower() == "none",
        )
        # #endregion

    return ok(data=result.user, tokens=tokens, message="User logged in successfully")


# Token Refresh


def _get_refresh_token(
    request: Request,
    client_type: ClientType,
) -> str:
    if client_type == ClientType.DRIVER:
        token = get_bearer_token(request)
        if not token:
            raise AuthenticationError("Driver app must send refresh token in Authorization: Bearer header")
        return token
    cookie_name = COOKIE_NAMES.get(client_type)
    if not cookie_name:
        raise AuthenticationError("Invalid client type for refresh")
    token = request.cookies.get(cookie_name)
    # #region agent log
    _ua = (request.headers.get("user-agent") or "")[:120]
    _is_safari = "Safari" in _ua and "Chrome" not in _ua and "Chromium" not in _ua
    logger.info(
        "agent_debug",
        session_id="89e7eb",
        hypothesis_id="A,B,C",
        location="routes.py:_get_refresh_token",
        message="refresh cookie lookup",
        client_type=client_type.value,
        cookie_name=cookie_name,
        has_token=bool(token),
        cookie_keys=list(request.cookies.keys()),
        origin=request.headers.get("origin"),
        is_safari=_is_safari,
        cookie_samesite=getattr(settings, "COOKIE_SAMESITE", None),
    )
    # #endregion
    if not token:
        raise AuthenticationError("Refresh token cookie missing or expired")
    return token


@session_router.post(
    "/refresh",
    response_model=AuthResponse[None],
    **REFRESH,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def refresh_tokens(
    request: Request,
    response: Response,
    auth_service: AuthServiceDep,
    client_type: ClientTypeDep,
) -> dict:
    raw_token = _get_refresh_token(request, client_type)
    tokens = await auth_service.refresh_tokens(
        raw_refresh_token=raw_token,
        client_type=client_type,
    )

    response_tokens = TokenData(
        access_token=tokens.access_token,
        access_token_expires_in=tokens.access_token_expires_in,
    )
    if client_type == ClientType.DRIVER:
        response_tokens.refresh_token = tokens.refresh_token
        response_tokens.refresh_token_expires_in = tokens.refresh_token_expires_in
    elif tokens.refresh_token:
        set_refresh_token_cookie(response, client_type, tokens.refresh_token)

    return ok(tokens=response_tokens, message="Tokens refreshed successfully")


# Logout


@session_router.post(
    "/logout",
    response_model=MessageResponse,
    **LOGOUT,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def logout(
    request: Request,
    response: Response,
    auth_service: AuthServiceDep,
    client_type: ClientTypeDep,
) -> dict:
    raw_token = _get_refresh_token(request, client_type)
    await auth_service.logout(raw_token)
    if client_type != ClientType.DRIVER:
        clear_refresh_token_cookie(response, client_type)
    return ok(message="Successfully logged out")


@session_router.post(
    "/logout-all",
    response_model=MessageResponse,
    **LOGOUT_ALL,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def logout_all(
    request: Request,
    response: Response,
    auth_service: AuthServiceDep,
    client_type: ClientTypeDep,
) -> dict:
    raw_token = _get_refresh_token(request, client_type)
    count = await auth_service.logout_all(raw_token)
    if client_type != ClientType.DRIVER:
        clear_refresh_token_cookie(response, client_type)
    return ok(message=f"Logged out from {count} session(s)")


# ── Session management (access-token based) ─────────

@session_router.get(
    "",
    response_model=SuccessResponse[ActiveSessionsResponse],
    **SESSION,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def get_sessions(
    request: Request,
    response: Response,
    auth_service: AuthServiceDep,
    user: CurrentUserDep,
) -> dict:
    result = await auth_service.list_sessions(user)
    return ok(result)


@session_router.post(
    "/logout-other",
    response_model=MessageResponse,
    **LOGOUT_OTHER,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def logout_other_sessions(
    request: Request,
    response: Response,
    auth_service: AuthServiceDep,
    user: CurrentUserDep,
) -> dict:
    count = await auth_service.logout_other_sessions(user)
    return ok(message=f"Logged out from {count} other session(s)")


@session_router.post(
    "/logout-session",
    response_model=MessageResponse,
    **LOGOUT_SESSION,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def logout_session(
    request: Request,
    response: Response,
    auth_service: AuthServiceDep,
    user: CurrentUserDep,
    data: LogoutSessionRequest,
) -> dict:
    session_id = str(data.session_id)
    count = await auth_service.logout_session(user, session_id)

    if user.sid is not None and user.sid == session_id and user.client_type != ClientType.DRIVER:
        clear_refresh_token_cookie(response, user.client_type)

    return ok(message=f"Logged out from session(s) (revoked={count})")


# Password


@router.post(
    "/change-password",
    response_model=MessageResponse,
    **CHANGE_PASSWORD,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def change_password(
    request: Request,
    data: ChangePasswordRequest,
    response: Response,
    auth_service: AuthServiceDep,
    user: CurrentUserDep,
) -> dict:
    await auth_service.change_password(
        user_id=user.id,
        current_password=data.current_password,
        new_password=data.new_password,
    )
    return ok(message="Password changed successfully. Please log in again.")


@router.post(
    "/request-invite-link",
    response_model=MessageResponse,
    **REQUEST_INVITE_LINK_REMINDER,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def request_invite_link_reminder(
    request: Request,
    data: InviteLinkReminderRequest,
    response: Response,
    auth_service: AuthServiceDep,
) -> dict:
    await auth_service.request_invite_link_reminder(str(data.email))
    return ok(message="If your account is pending activation you will receive an invite email ")


@router.post(
    "/request-password-reset",
    response_model=MessageResponse,
    **REQUEST_PASSWORD_RESET,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def request_password_reset(
    request: Request,
    data: PasswordResetRequest,
    response: Response,
    auth_service: AuthServiceDep,
    client_type: ClientTypeDep,
) -> dict:
    await auth_service.request_password_reset(data.email, client_type)
    return ok(message="If an account exists for this email, you will receive a reset code.")


@router.post(
    "/verify-password-reset-otp",
    response_model=SuccessResponse[PasswordResetSessionResponse],
    **VERIFY_PASSWORD_RESET_OTP,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def verify_password_reset_otp(
    request: Request,
    data: PasswordResetVerifyOtpRequest,
    response: Response,
    auth_service: AuthServiceDep,
    client_type: ClientTypeDep,
) -> dict:
    result = await auth_service.verify_password_reset_otp(
        str(data.email),
        data.otp,
        client_type,
    )
    token_preview = result["password_reset_token"][:8]
    return ok(
        data=PasswordResetSessionResponse(
            password_reset_token=result["password_reset_token"],
            expires_in=result["expires_in"],
            expires_at=result["expires_at"],
            message=(
                f"OTP verified. Send the token as the `X-Password-Reset-Token` header on POST /auth/confirm-password-reset. "
                f"Token starts with {token_preview}... Valid for {result['expires_in'] // 60} minutes."
            ),
        )
    )


@router.post(
    "/confirm-password-reset",
    response_model=MessageResponse,
    **CONFIRM_PASSWORD_RESET,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def confirm_password_reset(
    request: Request,
    data: PasswordResetConfirm,
    response: Response,
    auth_service: AuthServiceDep,
    x_password_reset_token: Annotated[
        str,
        Header(
            alias="X-Password-Reset-Token",
            min_length=64,
            max_length=64,
            pattern=r"^[a-f0-9]{64}$",
            description="Session token from POST /auth/verify-password-reset-otp.",
        ),
    ],
    client_type: ClientTypeDep,
) -> dict:
    await auth_service.confirm_password_reset(
        data.new_password,
        password_reset_token=x_password_reset_token.strip(),
        client_type=client_type,
    )
    return ok(message="Password has been reset. You can now log in.")


# ── Invites (Code-based activation flow) ──────────

invite_router = APIRouter(prefix="/invites")
@invite_router.post(
    "/validate",
    response_model=SuccessResponse[InviteValidateResponse],
    **VALIDATE_INVITE,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def validate_invite(
    request: Request,
    response: Response,
    x_invite_token: InviteTokenDep,
    auth_service: AuthServiceDep,
) -> dict:
    prefill = await auth_service.validate_invite(x_invite_token.strip())
    return ok(data=prefill)


@invite_router.post(
    "/activate",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    **ACTIVATE_INVITE,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def activate_invite(
    request: Request,
    response: Response,
    x_invite_token: InviteTokenDep,
    data: InviteActivateRequest,
    auth_service: AuthServiceDep,
) -> dict:
    await auth_service.complete_invite_activation(x_invite_token.strip(), data.password)
    return ok(message="Account successfully activated. You can now log in.")


# Verify email


@router.get(
    "/verify-email",
    response_model=MessageResponse,
    **VERIFY_EMAIL,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def verify_email(
    request: Request,
    response: Response,
    token: str,
    auth_service: AuthServiceDep,
) -> dict:
    await auth_service.verify_email(token)
    return ok(message="Email verified. You can now log in.")


# Me (current user info)


@router.get(
    "/me",
    response_model=SuccessResponse[UserBrief],
    **ME,
)
async def get_me(user: CurrentUserDep, auth_service: AuthServiceDep) -> dict:
    return ok(await auth_service.get_me(user))


router.include_router(session_router)
