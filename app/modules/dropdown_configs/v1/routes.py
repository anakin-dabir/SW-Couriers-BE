from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.deps import Allowed, AuditCtxDep, AuthUser, CurrentUserDep, SessionDep
from app.common.enums import PermissionLevel, Resource
from app.common.enums.user import UserRole
from app.common.exceptions import ForbiddenError
from app.common.response import ok
from app.common.schemas import SuccessResponse
from app.modules.dropdown_configs.enums import DropdownConfigKey
from app.modules.dropdown_configs.service import DropdownConfigService
from app.modules.dropdown_configs.v1.docs import (
    DC_LIST_ALL_VALUES_GROUPED,
    DC_LIST_KEYS,
    DC_LIST_VALUES,
    DC_REPLACE_VALUES,
)
from app.modules.dropdown_configs.v1.schemas import (
    DropdownKeyListItem,
    DropdownValuesByKeyResponse,
    DropdownValueReplaceRequest,
    DropdownValueResponse,
)
from app.modules.permission.bundling import effective_permission_level

router = APIRouter()

DropdownServiceDep = Annotated[DropdownConfigService, Depends(DropdownConfigService.dep)]

_DROPDOWN_READ_ROLES = frozenset(
    {
        UserRole.ADMIN.value,
        UserRole.SUPER_ADMIN.value,
    }
)


async def _require_dropdown_read(user: CurrentUserDep, session: SessionDep) -> AuthUser:
    """Read dropdown options: DYNAMIC_CONFIGS read, or vehicle management read (not SYSTEM_DEFAULTS)."""
    if user.role not in _DROPDOWN_READ_ROLES:
        raise ForbiddenError("This action requires ADMIN or SUPER_ADMIN")

    from app.modules.permission.service import PermissionService

    permissions = await PermissionService(session).resolve_permissions(user)
    dynamic_read = effective_permission_level(permissions, Resource.DYNAMIC_CONFIGS, PermissionLevel.READ)
    vehicle_read = effective_permission_level(permissions, Resource.VEHICLE_MANAGEMENT, PermissionLevel.READ)
    if max(dynamic_read, vehicle_read) < PermissionLevel.READ:
        raise ForbiddenError(
            "Insufficient permission: requires DYNAMIC_CONFIGS READ or VEHICLE_MANAGEMENT READ for dropdown configuration"
        )
    return user


DropdownReadDep = Annotated[AuthUser, Depends(_require_dropdown_read)]
DropdownWriteDep = Annotated[
    AuthUser,
    Allowed(UserRole.SUPER_ADMIN, UserRole.ADMIN, resource=Resource.SYSTEM_DEFAULTS, level=PermissionLevel.WRITE),
]


@router.get(
    "/keys",
    response_model=SuccessResponse[list[DropdownKeyListItem]],
    **DC_LIST_KEYS,
)
async def list_keys(
    _user: DropdownReadDep,
    svc: DropdownServiceDep,
    search: str | None = Query(default=None, min_length=1, max_length=120),
) -> dict:
    items = await svc.list_keys(search=search)
    return ok(data=items)


@router.get(
    "/values",
    response_model=SuccessResponse[list[DropdownValuesByKeyResponse]],
    **DC_LIST_ALL_VALUES_GROUPED,
)
async def list_all_values_grouped(
    _user: DropdownReadDep,
    svc: DropdownServiceDep,
) -> dict:
    items = await svc.list_all_values_grouped()
    return ok(data=items)


@router.get(
    "/keys/{key}/values",
    response_model=SuccessResponse[list[DropdownValueResponse]],
    **DC_LIST_VALUES,
)
async def list_values(
    key: DropdownConfigKey,
    _user: DropdownReadDep,
    svc: DropdownServiceDep,
) -> dict:
    items = await svc.list_values(key)
    return ok(data=items)


@router.patch(
    "/keys/{key}/values",
    response_model=SuccessResponse[list[DropdownValueResponse]],
    **DC_REPLACE_VALUES,
)
async def replace_values(
    key: DropdownConfigKey,
    body: DropdownValueReplaceRequest,
    _user: DropdownWriteDep,
    svc: DropdownServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    items = await svc.replace_values_for_key(key, body, ctx)
    return ok(data=items, message="Options saved successfully")
