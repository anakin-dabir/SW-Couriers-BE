"""User ORM model. Central identity model for all 7 roles.

Users are the core entity for authentication and RBAC. Each user has exactly
one role. B2B users are linked to an organization. Dispatchers are scoped
to a region.
"""

from datetime import datetime
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from app.common.enums import UserRole, UserStatus, UserTitle
from app.common.models import BaseModel
from app.common.utils import mask_email


class User(BaseModel):
    """User account — supports all roles with lockout."""

    __tablename__ = "users"

    # ── Identity ─────────────────────────────
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[UserTitle | None] = mapped_column(
        Enum(UserTitle, native_enum=False),
        nullable=True,
    )
    position_role: Mapped[str | None] = mapped_column(String(150), nullable=True)

    # ── Auth ─────────────────────────────────
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False),
        nullable=False,
        index=True,
    )
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, native_enum=False),
        nullable=False,
        default=UserStatus.INACTIVE,
    )
    # When true, user must change password after first successful login.
    # Used for driver onboarding / invitation-style credentials.
    force_password_change: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Login tracking ───────────────────────
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    inactive_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    inactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Lockout ──────────────────────────────
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Session generation (logout-all) ───────────────────────────────
    # Incremented on logout-all to invalidate all existing access tokens carrying `sv`.
    session_sv: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # ── Relations ────────────────────────────
    # B2B customer → organization
    organization_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Dispatcher → region scope (RBAC Layer 3)
    region_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("regions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── GDPR ─────────────────────────────────
    processing_objected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Backward-compatible alias used in some legacy tests/callers.
    is_verified = synonym("email_verified")

    # ── Profile ──────────────────────────────
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationships (explicit loading required) ──
    organization = relationship("Organization", lazy="raise", foreign_keys=[organization_id])
    region = relationship("Region", lazy="raise", foreign_keys=[region_id])

    def __repr__(self) -> str:
        return f"<User {mask_email(self.email)} role={self.role}>"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"
