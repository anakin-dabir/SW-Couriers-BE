"""Auth ORM models. Refresh tokens and user invites.

Refresh tokens are hashed (SHA-256) before storage. On each refresh,
the old token is revoked and a new one is issued (rotation).
Server-side revocation enables instant session termination.

Invites: admin/authorized users create invites; invitee opens link, sets password,
and is created as a user with the invited role and optional org/region scope.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import Base, UUIDMixin, _utc_now
from app.modules.auth.enums import ActivationLinkRequestStatus


class Invite(Base, UUIDMixin):
    """User invite: email + role + optional scope. Token stored hashed; single-use."""

    __tablename__ = "invites"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    invited_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        server_default=func.now(),
        nullable=False,
    )

    # Email delivery (offloaded to Arq worker; tracked for DLQ / resend)
    email_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)  # pending | sent | failed
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Code-based activation (Layer 4)
    verification_code_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    code_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("User", lazy="select", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return f"<Invite user_id={self.user_id}>"


class ActivationLinkRequest(Base, UUIDMixin):
    """Shared admin work item created when a pending user asks for a new invite link."""

    __tablename__ = "activation_link_requests"
    __table_args__ = (
        Index(
            "uq_activation_link_requests_one_pending_per_user",
            "requester_user_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    requester_user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[ActivationLinkRequestStatus] = mapped_column(
        Enum(ActivationLinkRequestStatus, native_enum=False),
        nullable=False,
        default=ActivationLinkRequestStatus.PENDING,
    )
    resolved_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_invite_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("invites.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
        nullable=False,
    )

    requester = relationship("User", lazy="select", foreign_keys=[requester_user_id])

    def __repr__(self) -> str:
        return f"<ActivationLinkRequest requester={self.requester_user_id} status={self.status}>"


class RefreshToken(Base, UUIDMixin):
    """Refresh token record. Hashed token stored for server-side revocation."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # SHA-256 hash of the actual token (never store raw)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    # JTI of the paired access token (for blacklisting on logout)
    access_jti: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Metadata
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Device/session tracking
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    # Logical device session id for UX + immediate per-session revocation.
    # Nullable initially for backward-compatible rollout.
    session_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sessions.session_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Timestamps (manual — not using BaseModel since no version needed)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<RefreshToken user={self.user_id} revoked={self.revoked}>"


class Session(Base):
    """Logical device session tracked for UX + immediate revocation."""

    __tablename__ = "sessions"

    # Logical stable identifier carried in JWTs
    session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        server_default=func.now(),
        nullable=False,
    )

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    inactivity_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    user = relationship("User", lazy="select", foreign_keys=[user_id])
