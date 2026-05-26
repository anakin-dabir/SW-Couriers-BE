"""Audit logging service — writes to the append-only audit_log table.

Provides a structured interface for recording security-relevant actions.
All auth events, data access, and state changes should go through here.
"""

import hashlib
import json
from datetime import datetime
from typing import Any

import structlog
from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import LogEvent
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.models import AuditLog
from app.modules.user.models import User

logger = structlog.get_logger()


def compute_integrity_hash(prev_hash: str | None, payload: dict[str, Any]) -> str:
    """Compute the chained SHA-256 hash for an audit row.

    Public helper so verification endpoints can recompute deterministically.
    """
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(((prev_hash or "") + canonical).encode("utf-8")).hexdigest()


def _enum_to_str(value: Any) -> str | None:
    """Return the underlying string value for str/enum, or None if missing."""
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def integrity_payload(entry: AuditLog) -> dict[str, Any]:
    """Canonical, order-independent representation of the audit row used to compute its hash."""
    return {
        "id": str(entry.id) if entry.id else None,
        "action": entry.action,
        "category": _enum_to_str(entry.category),
        "event_type": _enum_to_str(entry.event_type),
        "entity_type": entry.entity_type,
        "entity_id": str(entry.entity_id) if entry.entity_id else None,
        "user_id": str(entry.user_id) if entry.user_id else None,
        "organization_id": str(entry.organization_id) if entry.organization_id else None,
        "old_value": entry.old_value,
        "new_value": entry.new_value,
        "ip_address": entry.ip_address,
        "session_id": str(entry.session_id) if entry.session_id else None,
        "correlation_id": str(entry.correlation_id) if entry.correlation_id else None,
        "created_at": entry.created_at.isoformat() if isinstance(entry.created_at, datetime) else entry.created_at,
    }


class AuditService:
    """Writes structured audit entries to the append-only audit_log table."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        self.session = session
        self.request = request

    async def log(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: str | None = None,
        entity_ref: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        reason: str | None = None,
        organization_id: str | None = None,
        category: AuditCategory = AuditCategory.SYSTEM,
        event_type: AuditEventType | str = AuditEventType.SYSTEM_CONFIG_CHANGED,
        severity: str = "INFO",
        session_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Record a single audit event."""
        try:
            if ip_address is None:
                ip_address = self._extract_ip_address()
            if user_agent is None and self.request:
                user_agent = self.request.headers.get("user-agent")

            # Fallback to request-scoped audit context if caller didn't pass these explicitly.
            if (session_id is None or correlation_id is None) and self.request is not None:
                ctx = getattr(self.request.state, "audit_ctx", None)
                if ctx is not None:
                    if session_id is None:
                        session_id = ctx.session_id
                    if correlation_id is None:
                        correlation_id = ctx.correlation_id

            # Simple Environment Parsing
            browser, device, os = self._parse_ua(user_agent)

            if user_role is None and user_id is not None:
                user_role = await self._resolve_user_role(user_id)

            entry = AuditLog(
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                entity_ref=entity_ref,
                user_id=user_id,
                user_role=user_role,
                old_value=old_value,
                new_value=new_value,
                ip_address=ip_address,
                user_agent=user_agent,
                browser=browser,
                device=device,
                os=os,
                reason=reason,
                organization_id=organization_id,
                category=category,
                event_type=event_type,
                severity=severity,
                session_id=session_id,
                correlation_id=correlation_id,
            )

            async with self.session.begin_nested():
                if organization_id:
                    await self._acquire_org_audit_chain_lock(organization_id)
                # Fetch chain head BEFORE inserting so concurrent writers see consistent prev_hash.
                prev_hash = await self._latest_hash_for_org(organization_id)
                entry.prev_hash = prev_hash

                self.session.add(entry)
                await self.session.flush()

                year = datetime.now().year
                entry.audit_ref = f"AUD-{year}-{str(entry.id)[:8].upper()}"

                entry.integrity_hash = compute_integrity_hash(prev_hash, integrity_payload(entry))
                await self.session.flush()
        except Exception:
            logger.error(
                LogEvent.AUDIT_LOG_WRITE_FAILED,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                exc_info=True,
            )
            raise

    async def _acquire_org_audit_chain_lock(self, organization_id: str) -> None:
        """Serialize per-org integrity chain updates within the current transaction."""
        await self.session.execute(
            select(func.pg_advisory_xact_lock(func.hashtext(organization_id)))
        )

    async def _resolve_user_role(self, user_id: str) -> str | None: 
        result = await self.session.execute(select(User.role).where(User.id == user_id))
        raw = result.scalar_one_or_none()
        if raw is None:
            return None

        return raw.value if hasattr(raw, "value") else str(raw)

    async def _latest_hash_for_org(self, organization_id: str | None) -> str | None:
        """Return the integrity_hash of the most recent row in this organization's chain, if any."""
        if not organization_id:
            return None
        stmt = (
            select(AuditLog.integrity_hash)
            .where(
                AuditLog.organization_id == organization_id,
                AuditLog.integrity_hash.is_not(None),
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def _extract_ip_address(self) -> str | None:
        if not self.request:
            return None
        forwarded = self.request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip() or None
        return self.request.client.host if self.request.client else None

    def _parse_ua(self, ua_str: str | None) -> tuple[str, str, str]:
        if not ua_str:
            return "Unknown", "Unknown", "Unknown"
        ua = ua_str.lower()

        # 1. Browser
        browser = "Other"
        if "edg/" in ua: browser = "Microsoft Edge"
        elif "opr/" in ua or "opera" in ua: browser = "Opera"
        elif "vivaldi" in ua: browser = "Vivaldi"
        elif "chrome" in ua: browser = "Google Chrome"
        elif "firefox" in ua: browser = "Mozilla Firefox"
        elif "safari" in ua and "chrome" not in ua: browser = "Safari"

        # 2. OS
        os = "Other"
        if "windows nt 10.0" in ua: os = "Windows 11" if "chrome" in ua else "Windows 10"
        elif "windows nt 6.3" in ua: os = "Windows 8.1"
        elif "mac os x" in ua: os = "macOS"
        elif "iphone" in ua: os = "iOS"
        elif "android" in ua: os = "Android"
        elif "ubuntu" in ua: os = "Ubuntu"
        elif "debian" in ua: os = "Debian"
        elif "fedora" in ua: os = "Fedora"
        elif "linux" in ua: os = "Linux"

        # 3. Device
        device = "Desktop"
        if "mobi" in ua: device = "Mobile"
        if "tablet" in ua or "ipad" in ua: device = "Tablet"

        return browser, device, os
