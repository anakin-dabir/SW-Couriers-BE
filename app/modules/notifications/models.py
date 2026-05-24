"""Notification models: inbox, audit log, templates, preferences (wide), device tokens.

Preference tables use a **wide** shape: one row per (scope, notification_type, event),
with channel-specific columns (``email_enabled``, ``sms_enabled``,
``email_template_id``, ``sms_template_id``).

At override layers (user, organisation) the ``_enabled`` and ``_template_id`` columns
are nullable — ``NULL`` means "inherit from the next layer down in the cascade":

    user → org → system → hardcoded code default

The ``system_notification_defaults`` layer uses NOT NULL booleans since it sits
above the hardcoded code defaults and must always provide a concrete value.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, sql
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import BaseModel, BaseModelNoVersion


class SystemNotificationDefault(BaseModel):
    """System-wide default preferences (admin-editable, layer 2 in the cascade).

    Only ``B2B_CUSTOMER`` and ``RECIPIENT`` streams have system defaults.
    ``ADMIN_INTERNAL`` skips this layer (user → hardcoded).
    """

    __tablename__ = "system_notification_defaults"
    __table_args__ = (
        UniqueConstraint(
            "notification_type",
            "event",
            name="uq_sys_notif_default_type_event",
        ),
    )

    notification_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(50), nullable=False)

    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sql.true(), nullable=False)
    sms_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sql.true(), nullable=False)

    email_template_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("notification_templates.id", name="fk_sys_notif_default_email_tpl", ondelete="SET NULL"),
        nullable=True,
    )
    sms_template_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("notification_templates.id", name="fk_sys_notif_default_sms_tpl", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<SystemNotificationDefault {self.notification_type}/{self.event}>"


class OrgNotificationPreference(BaseModel):
    """Organisation-level overrides (layer 3).

    Applies to ``B2B_CUSTOMER`` and ``RECIPIENT`` streams for a given org.
    Any NULL column means "inherit from system defaults for that channel".
    """

    __tablename__ = "org_notification_preferences"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "notification_type",
            "event",
            name="uq_org_notif_pref_org_type_event",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notification_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(50), nullable=False)

    email_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sms_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    email_template_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("notification_templates.id", name="fk_org_notif_pref_email_tpl", ondelete="SET NULL"),
        nullable=True,
    )
    sms_template_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("notification_templates.id", name="fk_org_notif_pref_sms_tpl", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<OrgNotificationPreference org={self.organization_id} {self.notification_type}/{self.event}>"


class UserNotificationPreference(BaseModel):
    """Per-user overrides (layer 4) for ``ADMIN_INTERNAL`` or ``B2B_CUSTOMER`` streams.

    NULL columns inherit from the next layer down (org for B2B_CUSTOMER, or
    directly the hardcoded default for ADMIN_INTERNAL).
    """

    __tablename__ = "user_notification_preferences"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "notification_type",
            "event",
            name="uq_user_notif_pref_user_type_event",
        ),
    )

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notification_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(50), nullable=False)

    email_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sms_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    email_template_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("notification_templates.id", name="fk_user_notif_pref_email_tpl", ondelete="SET NULL"),
        nullable=True,
    )
    sms_template_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("notification_templates.id", name="fk_user_notif_pref_sms_tpl", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<UserNotificationPreference user={self.user_id} {self.notification_type}/{self.event}>"


class NotificationTemplate(BaseModel):
    """Admin-configurable notification template with variable placeholders.

    Linked to a preference row through the ``*_template_id`` columns. A
    template that is no longer referenced is hard-deleted as part of the reset
    flow.
    """

    __tablename__ = "notification_templates"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    variables: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sql.true(), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<NotificationTemplate {self.name} channel={self.channel}>"


class Notification(BaseModel):
    """User-facing inbox notification — created only when in-app is enabled."""

    __tablename__ = "notifications"

    recipient_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    notification_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<Notification {self.event} to={self.recipient_id}>"


class NotificationAuditLog(BaseModelNoVersion):
    """External channel delivery tracking — one row per event per recipient."""

    __tablename__ = "notification_audit_log"

    notification_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("notifications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    recipient_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    organization_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    notification_type: Mapped[str] = mapped_column(String(30), nullable=False)

    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    context_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    email_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    sms_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sms_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sms_external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    push_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    push_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    push_external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    def __repr__(self) -> str:
        return f"<NotificationAuditLog {self.event} type={self.notification_type}>"


class UserDeviceToken(BaseModel):
    """FCM device token for push notifications."""

    __tablename__ = "user_device_tokens"
    __table_args__ = (UniqueConstraint("device_token", name="uq_user_device_tokens_token"),)

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_token: Mapped[str] = mapped_column(String(500), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sql.true(), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<UserDeviceToken user={self.user_id} platform={self.platform}>"
