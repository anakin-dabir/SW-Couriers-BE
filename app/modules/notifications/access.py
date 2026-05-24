from typing import Annotated

from app.common.deps import Allowed, AllowedPolicy, AuthUser
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource

_ANY_ADMIN = (UserRole.ADMIN, UserRole.SUPER_ADMIN)

AdminNotificationReadPerm = Annotated[AuthUser, Allowed(*_ANY_ADMIN, resource=Resource.NOTIFICATIONS, level=PermissionLevel.READ)]
AdminNotificationWritePerm = Annotated[AuthUser, Allowed(*_ANY_ADMIN, resource=Resource.NOTIFICATIONS, level=PermissionLevel.WRITE)]
AdminOrB2BNotificationReadPerm = Annotated[AuthUser, Allowed(*_ANY_ADMIN, UserRole.CUSTOMER_B2B, resource=Resource.NOTIFICATIONS, level=PermissionLevel.READ)]
AdminOrB2BNotificationWritePerm = Annotated[AuthUser, Allowed(*_ANY_ADMIN, UserRole.CUSTOMER_B2B, resource=Resource.NOTIFICATIONS, level=PermissionLevel.WRITE)]
AdminOrgNotificationReadPerm = Annotated[
    AuthUser,
    Allowed(*_ANY_ADMIN, resource=Resource.ORGANIZATIONS, level=PermissionLevel.READ),
]
AdminOrgNotificationWritePerm = Annotated[
    AuthUser,
    Allowed(*_ANY_ADMIN, resource=Resource.ORGANIZATIONS, level=PermissionLevel.WRITE),
]

AdminOrB2BOrgNotificationReadPerm = Annotated[
    AuthUser,
    Allowed(
        policies=[
            AllowedPolicy(roles=_ANY_ADMIN, resource=Resource.ORGANIZATIONS, level=PermissionLevel.READ),
            AllowedPolicy(roles=(UserRole.CUSTOMER_B2B,), resource=Resource.NOTIFICATIONS, level=PermissionLevel.READ),
        ]
    ),
]

AdminOrB2BOrgNotificationWritePerm = Annotated[
    AuthUser,
    Allowed(
        policies=[
            AllowedPolicy(roles=_ANY_ADMIN, resource=Resource.ORGANIZATIONS, level=PermissionLevel.WRITE),
            AllowedPolicy(roles=(UserRole.CUSTOMER_B2B,), resource=Resource.NOTIFICATIONS, level=PermissionLevel.WRITE),
        ]
    ),
]

B2BNotificationReadPerm = Annotated[AuthUser, Allowed(UserRole.CUSTOMER_B2B, resource=Resource.NOTIFICATIONS, level=PermissionLevel.READ)]
B2BNotificationWritePerm = Annotated[AuthUser, Allowed(UserRole.CUSTOMER_B2B, resource=Resource.NOTIFICATIONS, level=PermissionLevel.WRITE)]