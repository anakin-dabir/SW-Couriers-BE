"""Delivery & Return Attempt Charges — global admin settings."""

from typing import Annotated

from fastapi import APIRouter, Depends
from starlette import status

from app.common.deps import Allowed, AuthUser
from app.common.enums.user import UserRole
from app.common.response import ok
from app.common.schemas import MessageResponse, SuccessResponse
from app.modules.delivery_attempts.service import DeliveryAttemptService
from app.modules.delivery_attempts.v1.schemas import (
    DeliveryAttemptConfigPatch,
    DeliveryAttemptConfigResponse,
    DeliveryAttemptConfigUpdate,
)

router = APIRouter()

AdminUserDep = Annotated[AuthUser, Allowed(UserRole.SUPER_ADMIN, UserRole.ADMIN)]
DeliveryAttemptServiceDep = Annotated[DeliveryAttemptService, Depends(DeliveryAttemptService.dep)]


@router.get(
    "",
    response_model=SuccessResponse[DeliveryAttemptConfigResponse],
    summary="Get global delivery & return attempt charges",
)
async def get_delivery_attempt_config(
    user: AdminUserDep,
    svc: DeliveryAttemptServiceDep,
) -> dict:
    """Return the global delivery and return attempt charge configuration singleton."""
    result = await svc.get_config()
    return ok(result)


@router.put(
    "",
    response_model=SuccessResponse[DeliveryAttemptConfigResponse],
    summary="Replace global delivery & return attempt charges",
)
async def update_delivery_attempt_config(
    data: DeliveryAttemptConfigUpdate,
    user: AdminUserDep,
    svc: DeliveryAttemptServiceDep,
) -> dict:
    """Replace the global config. max_* can be omitted and are derived from fee array lengths."""
    result = await svc.update_config(data, admin_user_id=user.id)
    return ok(result)


@router.post(
    "",
    response_model=SuccessResponse[DeliveryAttemptConfigResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create global delivery & return attempt charges singleton",
)
async def create_delivery_attempt_config(
    data: DeliveryAttemptConfigUpdate,
    user: AdminUserDep,
    svc: DeliveryAttemptServiceDep,
) -> dict:
    """Create global singleton config. max_* can be omitted and are derived from fee array lengths."""
    result = await svc.create_config(data, admin_user_id=user.id)
    return ok(result)


@router.patch(
    "",
    response_model=SuccessResponse[DeliveryAttemptConfigResponse],
    summary="Patch global delivery & return attempt charges (compact sequence)",
)
async def patch_delivery_attempt_config(
    data: DeliveryAttemptConfigPatch,
    user: AdminUserDep,
    svc: DeliveryAttemptServiceDep,
) -> dict:
    """Partially update global attempt charges with compact renumbering and len(array)-derived max_*."""
    result = await svc.patch_config(data, admin_user_id=user.id)
    return ok(result)


@router.delete(
    "",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete global delivery & return attempt charges",
)
async def delete_delivery_attempt_config(
    user: AdminUserDep,
    svc: DeliveryAttemptServiceDep,
) -> dict:
    """Delete the global config singleton. Admin only."""
    await svc.delete_config(admin_user_id=user.id)
    return ok(message="Global delivery attempt configuration deleted.")
