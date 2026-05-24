"""Shared access helpers for dashboard routes."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.common.deps import AuthUser
from app.common.enums import UserRole


def resolve_dashboard_organization_id(
    user: AuthUser,
    requested_organization_id: str | None,
) -> str | None:
    """Return org scope for dashboard metrics (None = global admin view)."""
    is_privileged = user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)
    if is_privileged:
        return requested_organization_id
    if not user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organisation context is required",
        )
    if requested_organization_id and requested_organization_id != user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access another organisation",
        )
    return user.organization_id
