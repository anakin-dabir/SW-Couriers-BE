from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.common.deps import Allowed, AuthUser
from app.common.enums import ROLE_TO_CLIENT_TYPE, ClientType, Job, UserRole
from app.common.exceptions import NotFoundError
from app.common.response import ok
from app.common.schemas import SuccessResponse
from app.common.utils import build_email_link
from app.core.queue import enqueue
from app.core.rate_limit import AUTH_RATE_LIMIT, limiter
from app.modules.auth.service import AuthService
from app.modules.user.v1.docs import SEND_INVITE
from app.modules.user.v1.schemas import SendInviteResponse

router = APIRouter()

AuthServiceDep = Annotated[AuthService, Depends(AuthService.dep)]


@router.post(
    "/{user_id}/invite",
    response_model=SuccessResponse[SendInviteResponse],
    status_code=status.HTTP_201_CREATED,
    **SEND_INVITE,
)
@limiter.limit(AUTH_RATE_LIMIT)
async def send_invite(
    request: Request,
    response: Response,
    user_id: str,
    user: Annotated[AuthUser, Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN)],
    auth_service: AuthServiceDep,
) -> dict:
    """Send an invite to an existing user. Email is enqueued via Arq for low p95 latency.

    Drivers go through the dedicated set-password flow (deep link to the driver app),
    everyone else gets the standard accept-invite landing-page link.
    """
    target = await auth_service.user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError(resource="user", id=user_id)

    if target.role == UserRole.DRIVER:
        result = await auth_service.issue_driver_activation_email(
            inviter=user, target_user_id=user_id
        )
        message = (
            "Invite created. Email is being sent."
            if result.sent
            else "Invite created. Set LINK_BASE_URL_DRIVER to enable driver activation emails."
        )
        return ok(
            data=SendInviteResponse(invite_id=result.invite_id, email=result.user.email),
            message=message,
        )

    r = await auth_service.create_invite(
        user,
        user_id,
    )
    if r.throttled:
        return ok(
            data=SendInviteResponse(invite_id=r.public_invite_id, email=r.user.email),
            message="Invite created. Email is being sent.",
        )

    client_type = ROLE_TO_CLIENT_TYPE.get(r.user.role, ClientType.CUSTOMER_B2C)
    invite_link = build_email_link(client_type, "accept-invite", r.raw_token or "")
    message = "Invite created. Email will be sent shortly."

    if invite_link:
        job = await enqueue(
            Job.SEND_INVITE_EMAIL,
            r.public_invite_id,
            r.user.email,
            r.user.first_name,
            invite_link,
            expires_days=AuthService.INVITE_EXPIRE_DAYS,
        )
        message = "Invite created. Email is being sent." if job is not None else "Invite created. Arq worker unavailable; email will be sent when worker starts."
    else:
        message = "Invite created. Set LINK_BASE_URL_<CLIENT_TYPE> or VERIFICATION_LINK_BASE_URL to enable invite emails."

    return ok(data=SendInviteResponse(invite_id=r.public_invite_id, email=r.user.email), message=message)
