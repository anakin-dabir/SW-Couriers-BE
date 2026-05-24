"""Org-scoped B2B access for company profile features.

ACCOUNT_OWNER always passes. Other org contacts need ORG_PROFILE permission overrides.
ADMIN and SUPER_ADMIN bypass permission checks (org-wide admin operations).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import AuthUser
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.exceptions import ForbiddenError
from app.modules.organizations.enums import ContactRole
from app.modules.permission.service import PermissionService

_PLATFORM_ADMIN_ROLES = frozenset({UserRole.ADMIN, UserRole.SUPER_ADMIN})


def is_platform_admin_role(role: str | UserRole | None) -> bool:
    """True for ADMIN and SUPER_ADMIN (platform staff with org-wide access)."""
    if role is None:
        return False
    if isinstance(role, UserRole):
        return role in _PLATFORM_ADMIN_ROLES
    try:
        return UserRole(role) in _PLATFORM_ADMIN_ROLES
    except ValueError:
        return False


async def assert_org_profile_access(
    session: AsyncSession,
    caller: AuthUser,
    org_id: str,
    caller_contact_role: ContactRole | None,
    min_level: PermissionLevel,
) -> None:
    """Raise ForbiddenError unless caller may perform org profile actions for this org.

    - ADMIN / SUPER_ADMIN: always allowed.
    - CUSTOMER_B2B: must belong to org_id; must have an org_contact row (caller_contact_role not None);
      ACCOUNT_OWNER always allowed; otherwise ORG_PROFILE >= min_level.
    - Any other role: forbidden (these routes are not for them).
    """
    if is_platform_admin_role(caller.role):
        return

    role = caller.role if isinstance(caller.role, str) else caller.role.value

    if role != UserRole.CUSTOMER_B2B:
        raise ForbiddenError("This action is only available to organisation members or admins.")

    if not caller.organization_id or str(caller.organization_id) != str(org_id):
        raise ForbiddenError("You do not have access to this organisation.")

    if caller_contact_role is None:
        raise ForbiddenError("You do not have access to this organisation.")

    if caller_contact_role == ContactRole.ACCOUNT_OWNER:
        return

    perm_service = PermissionService(session)
    await perm_service.check_permission(caller, Resource.ORG_PROFILE, min_level)


def assert_caller_org_scope(
    caller: AuthUser,
    org_id: str,
    *,
    message: str = "You do not have access to this organisation.",
) -> None:
    """If the caller is B2B, require JWT org_id to match the route ``org_id``; other roles no-op."""
    role = caller.role if isinstance(caller.role, str) else caller.role.value
    if role != UserRole.CUSTOMER_B2B:
        return
    if not caller.organization_id or str(caller.organization_id) != str(org_id):
        raise ForbiddenError(message)
