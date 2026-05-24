"""Audit log model and helper. Append-only table recording all meaningful actions.

Critical for GDPR accountability (Art. 5) and PCI audit trails.
This table NEVER has UPDATE or DELETE operations.
"""

from typing import TYPE_CHECKING
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import AppendOnlyModel, BaseModelNoVersion
from app.modules.audit.enums import AuditCategory

if TYPE_CHECKING:
    from app.modules.auth.models import Session
    from app.modules.user.models import User
    from app.modules.organizations.models import Organization


class AuditLog(AppendOnlyModel):
    """Append-only audit log — who did what, when, to which resource."""

    __tablename__ = "audit_log"

    # ── Who ──────────────────────────────────
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_role: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # ── What ─────────────────────────────────
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)  # e.g. "shipment.assigned", "user.created", "status.changed"
    category: Mapped[AuditCategory | None] = mapped_column(String(20), nullable=True, index=True)
    event_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="INFO", index=True) # INFO, NOTICE, WARNING, CRITICAL

    # ── Which resource ───────────────────────
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # e.g. "booking", "package", "user"
    entity_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)

    # ── State change ─────────────────────────
    old_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Context ──────────────────────────────
    organization_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    browser: Mapped[str | None] = mapped_column(String(50), nullable=True)
    device: Mapped[str | None] = mapped_column(String(100), nullable=True)
    os: Mapped[str | None] = mapped_column(String(50), nullable=True)
    audit_ref: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True) # e.g. AUD-2026-00018218
    entity_ref: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True) # e.g. SWC-ORG-2026-50917
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Session / correlation ───────────────
    # Linked auth session (logical device session) when an authenticated user triggered the event.
    session_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sessions.session_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Shared id across all audits emitted from the same HTTP request (powers "related events"/timeline).
    correlation_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        nullable=True,
        index=True,
    )

    # ── Integrity chain ──────────────────────
    # SHA-256 hash of (prev_hash || canonical payload). prev_hash points to the latest row in the
    # per-organization chain at insert time. NULL for legacy rows written before the column existed.
    integrity_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ── Relationships ────────────────────────
    user: Mapped["User | None"] = relationship("User", lazy="raise", foreign_keys=[user_id])
    organization: Mapped["Organization | None"] = relationship("Organization", lazy="raise", foreign_keys=[organization_id])
    session: Mapped["Session | None"] = relationship("Session", lazy="raise", foreign_keys=[session_id])

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} entity={self.entity_type}/{self.entity_id}>"


class AuditSavedView(BaseModelNoVersion):
    """User-defined filter configurations for the audit log dashboard."""

    __tablename__ = "audit_saved_views"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # The JSON filter configuration (category, severity, actor, event_type, etc.)
    filters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_default: Mapped[bool] = mapped_column(default=False, nullable=False)

    # ── Relationships ────────────────────────
    user: Mapped["User | None"] = relationship("User", lazy="raise", foreign_keys=[user_id])
    organization: Mapped["Organization"] = relationship("Organization", lazy="raise", foreign_keys=[organization_id])

    def __repr__(self) -> str:
        return f"<AuditSavedView {self.name} org={self.organization_id}>"
