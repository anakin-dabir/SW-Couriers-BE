"""Pydantic schemas for the permission module API."""

from __future__ import annotations

from pydantic import Field, field_validator

from app.common.enums.permission import PermissionLevel, Resource
from app.common.schemas import BaseSchema

# ── Request schemas ──────────────────────────────


class SetPermissionRequest(BaseSchema):
    """Set a single permission for a user."""

    resource: str = Field(description="Resource name (e.g. SHIPMENTS)")
    level: str = Field(description="Permission level: NONE, READ, or WRITE")

    @field_validator("resource")
    @classmethod
    def validate_resource(cls, v: str) -> str:
        try:
            Resource(v)
        except ValueError:
            valid = ", ".join(r.value for r in Resource)
            raise ValueError(f"Invalid resource '{v}'. Must be one of: {valid}") from None
        return v

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        try:
            PermissionLevel[v]
        except KeyError:
            raise ValueError(f"Invalid level '{v}'. Must be one of: NONE, READ, WRITE") from None
        return v


class BulkSetPermissionsRequest(BaseSchema):
    """Replace all permissions for a user."""

    permissions: list[SetPermissionRequest] = Field(
        min_length=1,
        description="List of resource + level pairs",
    )


# ── Response schemas ─────────────────────────────


class PermissionEntry(BaseSchema):
    """Single permission entry in the response."""

    resource: str
    level: str
    source: str = Field(description="'role_default' or 'override'")


class UserPermissionSummary(BaseSchema):
    """Full permission summary for a user."""

    user_id: str
    role: str
    permissions: list[PermissionEntry]


class PermissionUpdateResponse(BaseSchema):
    """Response after a permission change."""

    message: str
    user_id: str


class AvailableResourcesResponse(BaseSchema):
    """List of all available resources and levels (for admin UI dropdowns)."""

    resources: list[str]
    levels: list[str]
