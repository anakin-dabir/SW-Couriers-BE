"""Display labels and redaction for driver activity log APIs."""

from __future__ import annotations

import copy
import re
from typing import Any

from app.modules.audit.enums import AuditEventType
from app.modules.audit.models import AuditLog
from app.modules.user.models import User

_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|pwd|secret|token|authorization|refresh|access_jti|otp|cvv|pan|card_number|credit_card)",
    re.I,
)


def activity_user_type_badge(*, user_role: str | None, user_id: str | None, user: User | None) -> str:
    """Short label for the activity table User Type column."""
    if not user_id:
        return "System"
    resolved_role = user_role
    if not resolved_role and user is not None:
        raw = getattr(user, "role", None)
        if raw is not None:
            resolved_role = raw.value if hasattr(raw, "value") else str(raw)
    if not resolved_role:
        return "System"
    role = resolved_role.upper()
    if role == "DRIVER":
        return "Driver"
    if role in ("ADMIN", "SUPER_ADMIN"):
        return "Admin"
    if role == "WAREHOUSE_STAFF":
        return "Warehouse"
    return role.replace("_", " ").title()


def audit_category_str(log: AuditLog) -> str | None:
    c = log.category
    if c is None:
        return None
    return c.value if hasattr(c, "value") else str(c)


def audit_event_type_str(log: AuditLog) -> str | None:
    et = log.event_type
    if et is None:
        return None
    return et.value if hasattr(et, "value") else str(et)


def activity_event_label(log: AuditLog) -> str:
    """Human-readable Event column; stable mapping for common auth/fleet actions."""
    et = log.event_type
    et_val = et.value if hasattr(et, "value") else (et or "")

    mapping: dict[str, str] = {
        AuditEventType.LOGIN_SUCCESS.value: "Login",
        AuditEventType.LOGOUT_SUCCESS.value: "Logout",
        AuditEventType.LOGIN_FAILED.value: "Login failed",
        AuditEventType.PASSWORD_CHANGED.value: "Password change",
        AuditEventType.DOCUMENT_UPLOADED.value: "Document upload",
        AuditEventType.DOCUMENT_DELETED.value: "Document delete",
        AuditEventType.SHIFT_CREATED.value: "Shift assigned",
        AuditEventType.SHIFT_UPDATED.value: "Shift updated",
        AuditEventType.SHIFT_DELETED.value: "Shift deleted",
    }
    if et_val in mapping:
        return mapping[et_val]

    if log.reason and log.reason.strip():
        return log.reason.strip()

    action = (log.action or "").strip()
    if action:
        return action.replace(".", " ").replace("_", " ").strip().title()

    return et_val.replace("_", " ").title() if et_val else "Activity"


def parse_os_from_user_agent(ua_str: str | None) -> str:
    """Rough OS label from User-Agent (parity with org audit logs)."""
    if not ua_str:
        return "Unknown"
    ua = ua_str.lower()
    if "windows nt 10.0" in ua:
        return "Windows 11" if "chrome" in ua else "Windows 10"
    if "windows nt 6.3" in ua:
        return "Windows 8.1"
    if "windows nt 6.1" in ua:
        return "Windows 7"
    if "mac os x" in ua:
        return "macOS"
    if "iphone" in ua:
        return "iOS"
    if "android" in ua:
        return "Android"
    if "ubuntu" in ua:
        return "Ubuntu"
    if "debian" in ua:
        return "Debian"
    if "fedora" in ua:
        return "Fedora"
    if "linux" in ua:
        return "Linux"
    return "Other"


def actor_email(log: AuditLog, user: User | None) -> str | None:
    if user and user.email:
        return user.email
    if not log.user_id:
        return None
    return None


def redact_audit_json(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Remove sensitive keys and truncate large string values for detail API."""
    if data is None:
        return None
    out: dict[str, Any] = {}
    for key, val in data.items():
        if _SENSITIVE_KEY_RE.search(str(key)):
            out[key] = "[REDACTED]"
            continue
        if isinstance(val, dict):
            out[key] = redact_audit_json(val) or {}
        elif isinstance(val, list):
            out[key] = _redact_list(val)
        elif isinstance(val, str) and len(val) > 2000:
            out[key] = val[:2000] + "…"
        else:
            out[key] = copy.deepcopy(val)
    return out


def _redact_list(items: list[Any]) -> list[Any]:
    out: list[Any] = []
    for item in items[:500]:
        if isinstance(item, dict):
            out.append(redact_audit_json(item) or {})
        elif isinstance(item, str) and len(item) > 500:
            out.append(item[:500] + "…")
        else:
            out.append(copy.deepcopy(item))
    if len(items) > 500:
        out.append(f"[{len(items) - 500} more items omitted]")
    return out
