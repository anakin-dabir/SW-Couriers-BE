"""Shared helpers for audit read APIs."""

from sqlalchemy import or_
from sqlalchemy.sql import ColumnElement

from app.modules.audit.models import AuditLog

_ADMIN_ACTOR_ROLES = ("ADMIN", "SUPER_ADMIN")


def audit_actor_label(user_role: str | None) -> str:
    """Map stored user_role to UI actor bucket (Admin vs Client)."""
    if user_role in _ADMIN_ACTOR_ROLES:
        return "Admin"
    return "Client"


def audit_actor_filter_clause(actor: str | None) -> ColumnElement | None:
    """SQL clause for actor=Admin|Client query filters."""
    if not actor:
        return None
    key = actor.lower()
    if key == "admin":
        return AuditLog.user_role.in_(_ADMIN_ACTOR_ROLES)
    if key == "client":
        return or_(
            AuditLog.user_role.is_(None),
            AuditLog.user_role.notin_(_ADMIN_ACTOR_ROLES),
        )
    return None
