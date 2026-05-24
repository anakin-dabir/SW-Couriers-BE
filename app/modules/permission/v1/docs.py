"""OpenAPI documentation entries for permission endpoints."""

from __future__ import annotations

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry

GET_USER_PERMISSIONS = create_doc_entry(
    "Get user's resolved permissions",
    {
        200: success_entry(
            "Permission summary",
            data={
                "user_id": "00000000-0000-0000-0000-000000000000",
                "role": "WAREHOUSE_STAFF",
                "permissions": [
                    {"resource": "DASHBOARD", "level": "READ", "source": "role_default"},
                    {"resource": "SHIPMENTS", "level": "WRITE", "source": "override"},
                ],
            },
        ),
        401: error_401_entry(),
        403: error_entry(
            "Not allowed (admin only)",
            code="FORBIDDEN",
            message="This action requires one of: ADMIN",
        ),
        404: error_entry(
            "User not found",
            code="NOT_FOUND",
            message="user with id '...' not found",
        ),
    },
)

SET_PERMISSION = create_doc_entry(
    "Set a single permission for a user",
    {
        200: success_entry(
            "Permission updated",
            data={"message": "Permission updated", "user_id": "..."},
        ),
        401: error_401_entry(),
        403: error_entry(
            "Not allowed (admin only)",
            code="FORBIDDEN",
            message="This action requires one of: ADMIN",
        ),
        404: error_entry(
            "User not found",
            code="NOT_FOUND",
            message="user with id '...' not found",
        ),
    },
)

BULK_SET_PERMISSIONS = create_doc_entry(
    "Replace all permissions for a user",
    {
        200: success_entry(
            "Permissions replaced",
            data={"message": "Permissions updated", "user_id": "..."},
        ),
        401: error_401_entry(),
        403: error_entry(
            "Not allowed (admin only)",
            code="FORBIDDEN",
            message="This action requires one of: ADMIN",
        ),
        404: error_entry(
            "User not found",
            code="NOT_FOUND",
            message="user with id '...' not found",
        ),
    },
)

RESET_PERMISSIONS = create_doc_entry(
    "Reset user permissions to role defaults",
    {
        200: success_entry(
            "Permissions reset",
            data={"message": "Permissions reset to role defaults", "user_id": "..."},
        ),
        401: error_401_entry(),
        403: error_entry(
            "Not allowed (admin only)",
            code="FORBIDDEN",
            message="This action requires one of: ADMIN",
        ),
        404: error_entry(
            "User not found",
            code="NOT_FOUND",
            message="user with id '...' not found",
        ),
    },
)

GET_AVAILABLE_RESOURCES = create_doc_entry(
    "List all available resources and permission levels",
    {
        200: success_entry(
            "Resources and levels",
            data={
                "resources": ["DASHBOARD", "SHIPMENTS", "..."],
                "levels": ["NONE", "READ", "WRITE"],
            },
        ),
        401: error_401_entry(),
        403: error_entry(
            "Not allowed (admin only)",
            code="FORBIDDEN",
            message="This action requires one of: ADMIN",
        ),
    },
)

GET_MY_PERMISSIONS = create_doc_entry(
    "Get current user's own permissions",
    {
        200: success_entry(
            "Your permissions",
            data={
                "user_id": "00000000-0000-0000-0000-000000000000",
                "role": "WAREHOUSE_STAFF",
                "permissions": [
                    {"resource": "DASHBOARD", "level": "READ", "source": "role_default"},
                ],
            },
        ),
        401: error_401_entry(),
    },
)
