"""Permission management routes — admin endpoints + self-lookup.

Admin can view/set/reset permissions for any user.
Any authenticated user can view their own resolved permissions.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.common.deps import Allowed, AuthUser, CurrentUserDep, SessionDep
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.exceptions import NotFoundError
from app.common.response import ok
from app.common.schemas import SuccessResponse
from app.modules.permission.bundling import RESOURCES_ASSIGNABLE_BY_ADMIN
from app.modules.permission.service import PermissionService
from app.modules.permission.v1.docs import (
    BULK_SET_PERMISSIONS,
    GET_AVAILABLE_RESOURCES,
    GET_MY_PERMISSIONS,
    GET_USER_PERMISSIONS,
    RESET_PERMISSIONS,
    SET_PERMISSION,
)
from app.modules.permission.v1.schemas import (
    AvailableResourcesResponse,
    BulkSetPermissionsRequest,
    PermissionEntry,
    PermissionUpdateResponse,
    SetPermissionRequest,
    UserPermissionSummary,
)
from app.modules.user.repository import UserRepository

router = APIRouter()


PermissionServiceDep = Annotated[PermissionService, Depends(PermissionService.dep)]


# Self-lookup (any authenticated user)


@router.get(
    "/me",
    response_model=SuccessResponse[UserPermissionSummary],
    **GET_MY_PERMISSIONS,
)
async def get_my_permissions(
    user: CurrentUserDep,
    perm_service: PermissionServiceDep,
) -> dict:
    """Get the current user's own resolved permissions."""
    summary = await perm_service.get_user_permission_summary(user)
    return ok(
        UserPermissionSummary(
            user_id=user.id,
            role=user.role,
            permissions=[PermissionEntry(resource=r, level=p["level"], source=p["source"]) for r, p in summary.items()],
        )
    )


# Admin: available resources


@router.get(
    "/resources",
    response_model=SuccessResponse[AvailableResourcesResponse],
    **GET_AVAILABLE_RESOURCES,
)
async def get_available_resources(
    user: Annotated[AuthUser, Allowed(UserRole.ADMIN)],
) -> dict:
    """List all resources and permission levels (for admin UI dropdowns)."""
    return ok(
        AvailableResourcesResponse(
            resources=[r.value for r in sorted(RESOURCES_ASSIGNABLE_BY_ADMIN, key=lambda x: x.value)],
            levels=[lev.name for lev in PermissionLevel],
        )
    )


# Admin: view user permissions


@router.get(
    "/{user_id}",
    response_model=SuccessResponse[UserPermissionSummary],
    **GET_USER_PERMISSIONS,
)
async def get_user_permissions(
    user_id: str,
    user: Annotated[AuthUser, Allowed(UserRole.ADMIN)],
    session: SessionDep,
    perm_service: PermissionServiceDep,
) -> dict:
    """Get resolved permissions for a specific user (admin only)."""
    target_user = await UserRepository(session).get_by_id(user_id)
    if target_user is None:
        raise NotFoundError(resource="user", id=user_id)

    summary = await perm_service.get_user_permission_summary(target_user)
    return ok(
        UserPermissionSummary(
            user_id=target_user.id,
            role=target_user.role,
            permissions=[PermissionEntry(resource=r, level=p["level"], source=p["source"]) for r, p in summary.items()],
        )
    )


# Admin: set single permission


@router.put(
    "/{user_id}",
    response_model=SuccessResponse[PermissionUpdateResponse],
    **SET_PERMISSION,
)
async def set_permission(
    user_id: str,
    data: SetPermissionRequest,
    user: Annotated[AuthUser, Allowed(UserRole.ADMIN)],
    perm_service: PermissionServiceDep,
) -> dict:
    """Set a single permission override for a user (admin only)."""
    resource = Resource(data.resource)
    level = PermissionLevel[data.level]

    await perm_service.set_permission(
        target_user_id=user_id,
        resource=resource,
        level=level,
        granted_by=user.id,
    )
    return ok(PermissionUpdateResponse(message="Permission updated", user_id=user_id))


# Admin: bulk set permissions


@router.put(
    "/{user_id}/bulk",
    response_model=SuccessResponse[PermissionUpdateResponse],
    **BULK_SET_PERMISSIONS,
)
async def bulk_set_permissions(
    user_id: str,
    data: BulkSetPermissionsRequest,
    user: Annotated[AuthUser, Allowed(UserRole.ADMIN)],
    perm_service: PermissionServiceDep,
) -> dict:
    """Replace all permission overrides for a user (admin only)."""
    permissions = {Resource(p.resource): PermissionLevel[p.level] for p in data.permissions}
    await perm_service.bulk_set_permissions(
        target_user_id=user_id,
        permissions=permissions,
        granted_by=user.id,
    )
    return ok(PermissionUpdateResponse(message="Permissions updated", user_id=user_id))


# Admin: reset to defaults


@router.delete(
    "/{user_id}",
    response_model=SuccessResponse[PermissionUpdateResponse],
    **RESET_PERMISSIONS,
)
async def reset_permissions(
    user_id: str,
    user: Annotated[AuthUser, Allowed(UserRole.ADMIN)],
    perm_service: PermissionServiceDep,
) -> dict:
    """Remove all overrides and reset to role defaults (admin only)."""
    await perm_service.reset_to_defaults(
        target_user_id=user_id,
        admin_user_id=user.id,
    )
    return ok(
        PermissionUpdateResponse(
            message="Permissions reset to role defaults",
            user_id=user_id,
        )
    )
