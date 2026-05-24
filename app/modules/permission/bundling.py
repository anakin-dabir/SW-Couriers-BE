"""Permission bundling — DYNAMIC_CONFIGS write is governed by SYSTEM_DEFAULTS.

DYNAMIC_CONFIGS read stays separate so vehicle screens can grant read-only dropdown access
without system configuration write.
"""

from __future__ import annotations

from app.common.enums.permission import PermissionLevel, Resource

# Deprecated resources are not assignable; use the replacement named in Resource enum comments.
_NON_ASSIGNABLE_RESOURCES: frozenset[Resource] = frozenset(
    {
        Resource.STATUS_AUTOMATION_RULES,
        Resource.INVOICES,
        Resource.ACCOUNT_STATEMENTS,
        Resource.REQUESTS,
    }
)
RESOURCES_ASSIGNABLE_BY_ADMIN: frozenset[Resource] = frozenset(
    r for r in Resource if r not in _NON_ASSIGNABLE_RESOURCES
)


def effective_permission_level(
    permissions: dict[Resource, PermissionLevel],
    resource: Resource,
    required_level: PermissionLevel,
) -> PermissionLevel:
    """Return effective level, applying SYSTEM_DEFAULTS bundle for dropdown write only."""
    direct = permissions.get(resource, PermissionLevel.NONE)
    if resource == Resource.DYNAMIC_CONFIGS and required_level >= PermissionLevel.WRITE:
        bundle = permissions.get(Resource.SYSTEM_DEFAULTS, PermissionLevel.NONE)
        return max(direct, bundle)
    return direct


def is_assignable_resource(resource: Resource) -> bool:
    """Whether admins may set per-user overrides for this resource in the permission UI."""
    return resource in RESOURCES_ASSIGNABLE_BY_ADMIN
