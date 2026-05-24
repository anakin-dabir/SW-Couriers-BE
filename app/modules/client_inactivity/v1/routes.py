"""Client inactivity configuration API (Dynamic Configs / System Defaults)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.common.deps import Allowed, AuthUser
from app.common.enums import PermissionLevel, Resource, UserRole
from app.common.response import ok
from app.common.schemas import SuccessResponse
from app.modules.client_inactivity.service import ClientInactivityService
from app.modules.client_inactivity.v1.schemas import ClientInactivityConfigPatch, ClientInactivityConfigResponse

router = APIRouter()

ServiceDep = Annotated[ClientInactivityService, Depends(ClientInactivityService.dep)]
AdminReadDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.SYSTEM_DEFAULTS, level=PermissionLevel.READ),
]
AdminWriteDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.SYSTEM_DEFAULTS, level=PermissionLevel.WRITE),
]


@router.get(
    "",
    response_model=SuccessResponse[ClientInactivityConfigResponse],
    summary="Get client inactivity configuration",
)
async def get_client_inactivity_config(
    user: AdminReadDep,
    svc: ServiceDep,
) -> dict:
    """Return the global B2B client inactivity threshold (Dynamic Configs tab)."""
    return ok(await svc.get_config())


@router.patch(
    "",
    response_model=SuccessResponse[ClientInactivityConfigResponse],
    summary="Update client inactivity configuration",
)
async def patch_client_inactivity_config(
    data: ClientInactivityConfigPatch,
    user: AdminWriteDep,
    svc: ServiceDep,
) -> dict:
    """Update enabled flag and/or inactivity threshold days."""
    result = await svc.patch_config(data, admin_user_id=user.id)
    return ok(result)
