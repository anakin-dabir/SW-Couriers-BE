"""Permission service — business logic for ACL resolution and management.

Resolution flow:
  1. Check Redis cache for merged permission set
  2. On cache miss: load role defaults + DB overrides, merge, cache result
  3. Return resolved PermissionLevel for the requested Resource

Management (admin only):
  - Grant / revoke individual overrides
  - Bulk-set all permissions for a user
  - Reset to role defaults
"""

import structlog
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import UserLike
from app.common.enums import LogEvent
from app.common.enums.permission import PermissionLevel, Resource
from app.common.enums.user import UserRole, UserStatus
from app.common.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.permission.bundling import effective_permission_level, is_assignable_resource
from app.modules.permission.defaults import get_role_defaults
from app.modules.permission.repository import PermissionRepository
from app.modules.user.models import User
from app.modules.user.repository import UserRepository

logger = structlog.get_logger()


class PermissionService(BaseService):
    """Resolves and manages user permissions (role defaults + per-user overrides)."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._perm_repo = PermissionRepository(session)
        self._user_repo = UserRepository(session)
        self._audit = AuditService(session)

    # Resolution

    async def resolve_permissions(self, user: UserLike) -> dict[Resource, PermissionLevel]:
        """Return the full resolved permission set for a user.

        Checks Redis cache first, then falls back to DB + defaults merge.
        """
        user_id = str(user.id)
        cached = await PermissionRepository.get_cached_permissions(user_id)
        if cached is not None:
            return cached

        logger.debug(LogEvent.PERMISSION_CACHE_MISS, user_id=user_id)

        role = UserRole(user.role)
        defaults = get_role_defaults(role)
        overrides = await self._perm_repo.get_overrides_for_user(user_id)

        merged = {**defaults, **overrides}

        await PermissionRepository.set_cached_permissions(user_id, merged)
        return merged

    async def check_permission(
        self,
        user: UserLike,
        resource: Resource,
        required_level: PermissionLevel,
    ) -> None:
        """Raise ForbiddenError if user lacks the required permission level.

        This is the core enforcement method called by the RequirePermission dependency.
        """
        permissions = await self.resolve_permissions(user)
        user_level = effective_permission_level(permissions, resource, required_level)

        if user_level < required_level:
            logger.warning(
                LogEvent.PERMISSION_DENIED,
                user_id=user.id,
                resource=resource.value,
                required=required_level.name,
                actual=user_level.name,
            )
            raise ForbiddenError(f"Insufficient permission on {resource.value}: " f"requires {required_level.name}, you have {user_level.name}")

    async def get_user_permission_summary(self, user: UserLike) -> dict[str, dict[str, str]]:
        """Return the full permission summary for admin display.

        Returns a dict like:
          {"DASHBOARD": {"level": "READ", "source": "role_default"},
           "USERS":     {"level": "WRITE", "source": "override"}, ...}
        """
        role = UserRole(user.role)
        defaults = get_role_defaults(role)
        overrides = await self._perm_repo.get_overrides_for_user(str(user.id))

        summary: dict[str, dict[str, str]] = {}
        for resource in Resource:
            if resource in overrides:
                summary[resource.value] = {
                    "level": PermissionLevel(overrides[resource]).name,
                    "source": "override",
                }
            else:
                summary[resource.value] = {
                    "level": PermissionLevel(defaults.get(resource, PermissionLevel.NONE)).name,
                    "source": "role_default",
                }
        return summary

    async def list_active_admin_recipient_ids_for_resource(
        self,
        *,
        resource: Resource,
        min_level: PermissionLevel,
    ) -> list[str]:
        stmt = select(User).where(
            User.role.in_((UserRole.ADMIN, UserRole.SUPER_ADMIN)),
            User.status == UserStatus.ACTIVE,
        )
        result = await self._session.execute(stmt)
        users = list(result.scalars().all())
        out: list[str] = []
        for u in users:
            perms = await self.resolve_permissions(u)
            if effective_permission_level(perms, resource, min_level) >= min_level:
                out.append(str(u.id))
        return out

    # Management (admin operations)

    async def set_permission(
        self,
        target_user_id: str,
        resource: Resource,
        level: PermissionLevel,
        granted_by: str,
    ) -> None:
        """Set a single permission override for a user.

        If level matches the role default, the override is removed (no point storing it).
        """
        target_user = await self._user_repo.get_by_id(target_user_id)
        if target_user is None:
            raise NotFoundError(resource="user", id=target_user_id)
        if not is_assignable_resource(resource):
            raise ValidationError(
                f"Permission overrides for {resource.value} are not assignable; use SYSTEM_DEFAULTS instead.",
                details=[
                    {
                        "field": "resource",
                        "message": "Use SYSTEM_DEFAULTS for status automation and dropdown configuration write access",
                        "type": "value_error",
                    }
                ],
            )

        role = UserRole(target_user.role)
        role_default = get_role_defaults(role).get(resource, PermissionLevel.NONE)

        if level == role_default:
            await self._perm_repo.delete_override(target_user_id, resource)
        else:
            await self._perm_repo.upsert(target_user_id, resource, level, granted_by)

        await PermissionRepository.invalidate_cache(target_user_id)

        await self._audit.log(
            action="permission.set",
            entity_type="user_permission",
            entity_id=target_user_id,
            user_id=granted_by,
            new_value={
                "resource": resource.value,
                "level": level.name,
                "is_override": level != role_default,
            },
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.ROLE_ASSIGNED,
        )
        logger.info(
            LogEvent.PERMISSION_GRANTED,
            target_user_id=target_user_id,
            resource=resource.value,
            level=level.name,
            granted_by=granted_by,
        )

    async def bulk_set_permissions(
        self,
        target_user_id: str,
        permissions: dict[Resource, PermissionLevel],
        granted_by: str,
    ) -> None:
        """Replace all permission overrides for a user.

        Only stores entries that differ from the role default.
        """
        target_user = await self._user_repo.get_by_id(target_user_id)
        if target_user is None:
            raise NotFoundError(resource="user", id=target_user_id)

        role = UserRole(target_user.role)
        defaults = get_role_defaults(role)

        non_assignable = [r for r in permissions if not is_assignable_resource(r)]
        if non_assignable:
            names = ", ".join(r.value for r in non_assignable)
            raise ValidationError(
                f"Cannot assign overrides for: {names}. Use SYSTEM_DEFAULTS for bundled configuration access.",
                details=[
                    {
                        "field": "permissions",
                        "message": "Use SYSTEM_DEFAULTS for bundled configuration write access",
                        "type": "value_error",
                    }
                ],
            )

        overrides_only = {r: lev for r, lev in permissions.items() if lev != defaults.get(r, PermissionLevel.NONE)}

        await self._perm_repo.bulk_set(target_user_id, overrides_only, granted_by)
        await PermissionRepository.invalidate_cache(target_user_id)

        await self._audit.log(
            action="permission.bulk_set",
            entity_type="user_permission",
            entity_id=target_user_id,
            user_id=granted_by,
            new_value={
                "overrides": {r.value: lev.name for r, lev in overrides_only.items()},
                "total_overrides": len(overrides_only),
            },
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.ROLE_ASSIGNED,
        )
        logger.info(
            LogEvent.PERMISSION_BULK_SET,
            target_user_id=target_user_id,
            overrides_count=len(overrides_only),
            granted_by=granted_by,
        )

    async def reset_to_defaults(
        self,
        target_user_id: str,
        admin_user_id: str,
    ) -> None:
        """Remove all overrides for a user (revert to pure role defaults)."""
        target_user = await self._user_repo.get_by_id(target_user_id)
        if target_user is None:
            raise NotFoundError(resource="user", id=target_user_id)

        deleted = await self._perm_repo.delete_all_for_user(target_user_id)
        await PermissionRepository.invalidate_cache(target_user_id)

        await self._audit.log(
            action="permission.reset",
            entity_type="user_permission",
            entity_id=target_user_id,
            user_id=admin_user_id,
            new_value={"overrides_removed": deleted},
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.ROLE_REVOKED,
        )
        logger.info(
            LogEvent.PERMISSION_REVOKED,
            target_user_id=target_user_id,
            overrides_removed=deleted,
            admin_user_id=admin_user_id,
        )
