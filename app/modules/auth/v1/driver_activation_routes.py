"""Public driver activation APIs (deep link → set password → login).

Token transport matches web invites: `X-Invite-Token` header (same as `/auth/invites/*`),
not JSON body — read `token` from the deep link query string client-side.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.common.response import ok
from app.common.schemas import MessageResponse, SuccessResponse
from app.core.rate_limit import AUTH_RATE_LIMIT, limiter
from app.modules.auth.invite_header import InviteTokenDep
from app.modules.auth.service import AuthService
from app.modules.auth.v1.docs import (
    DRIVER_ACTIVATION_RESEND,
    DRIVER_ACTIVATION_SET_PASSWORD,
    DRIVER_ACTIVATION_VALIDATE,
)
from app.modules.auth.v1.schemas import (
    DriverActivationResendRequest,
    DriverActivationValidateResponse,
    InviteActivateRequest,
)

router = APIRouter(prefix="/driver-activation")

AuthServiceDep = Annotated[AuthService, Depends(AuthService.dep)]


@router.post(
    "/validate",
    response_model=SuccessResponse[DriverActivationValidateResponse],
    **DRIVER_ACTIVATION_VALIDATE,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def validate_driver_activation(
    request: Request,
    response: Response,
    x_invite_token: InviteTokenDep,
    auth_service: AuthServiceDep,
) -> dict:
    raw = await auth_service.validate_driver_activation_token(x_invite_token.strip())
    return ok(data=DriverActivationValidateResponse.model_validate(raw))


@router.post(
    "/set-password",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    **DRIVER_ACTIVATION_SET_PASSWORD,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def set_password_driver_activation(
    request: Request,
    response: Response,
    x_invite_token: InviteTokenDep,
    data: InviteActivateRequest,
    auth_service: AuthServiceDep,
) -> dict:
    await auth_service.complete_driver_activation(x_invite_token.strip(), data.password)
    return ok(message="Password set successfully. You can now log in with the driver app.")


@router.post(
    "/resend",
    response_model=MessageResponse,
    **DRIVER_ACTIVATION_RESEND,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def resend_driver_activation(
    request: Request,
    response: Response,
    body: DriverActivationResendRequest,
    auth_service: AuthServiceDep,
) -> dict:
    await auth_service.resend_driver_activation_public(str(body.email))
    return ok(message="If this email is eligible, a new activation link has been sent.")
