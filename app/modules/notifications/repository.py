"""Notification repository — data-access layer for the wide preference tables,
templates, devices, inbox, and audit log.

Preference tables use a **wide shape**: one row per
``(scope, notification_type, event)`` with per-channel columns.
Resolution is done in the service layer by reading the three preference
tables (user / org / system) and applying a channel-wise ``COALESCE`` with
the hardcoded defaults.

No business logic here — that lives in the service layer.
"""

# pyright: reportAttributeAccessIssue=false
# CursorResult.rowcount exists at runtime but is missing from SQLAlchemy's type stubs

import json
from datetime import UTC, datetime
from uuid import uuid4

import structlog
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.repository import BaseRepository
from app.modules.notifications.enums import (
    NotificationChannel,
    NotificationEvent,
    NotificationType,
)
from app.modules.notifications.defaults.preferences import get_event_channel_default
from app.modules.notifications.models import (
    Notification,
    NotificationAuditLog,
    NotificationTemplate,
    OrgNotificationPreference,
    SystemNotificationDefault,
    UserDeviceToken,
    UserNotificationPreference,
)
from app.modules.notifications.types import SystemDefaultRow

logger = structlog.get_logger()


_SYS_DEFAULTS_CACHE_PREFIX = "notif:sys_defaults"
_SYS_DEFAULTS_CACHE_TTL_SECONDS = 60 * 60 * 24


def _sys_defaults_cache_key(notification_type: NotificationType) -> str:
    return f"{_SYS_DEFAULTS_CACHE_PREFIX}:{notification_type.value}"


def _sys_default_to_view(row: SystemNotificationDefault) -> SystemDefaultRow:
    return SystemDefaultRow(
        id=row.id,
        notification_type=row.notification_type,
        event=row.event,
        email_enabled=row.email_enabled,
        sms_enabled=row.sms_enabled,
        email_template_id=row.email_template_id,
        sms_template_id=row.sms_template_id,
    )


def _sys_default_to_cache_dict(row: SystemNotificationDefault) -> dict:
    return {
        "id": row.id,
        "notification_type": row.notification_type,
        "event": row.event,
        "email_enabled": row.email_enabled,
        "sms_enabled": row.sms_enabled,
        "email_template_id": row.email_template_id,
        "sms_template_id": row.sms_template_id,
    }


def _cache_dict_to_view(data: dict) -> SystemDefaultRow:
    return SystemDefaultRow(
        id=data["id"],
        notification_type=data["notification_type"],
        event=data["event"],
        email_enabled=data["email_enabled"],
        sms_enabled=data["sms_enabled"],
        email_template_id=data.get("email_template_id"),
        sms_template_id=data.get("sms_template_id"),
    )


# Preference update payload helpers


def _enabled_col(channel: NotificationChannel) -> str:
    return "email_enabled" if channel == NotificationChannel.EMAIL else "sms_enabled"


def _template_col(channel: NotificationChannel) -> str:
    return "email_template_id" if channel == NotificationChannel.EMAIL else "sms_template_id"


# System defaults repository (layer 2 — NOT NULL booleans)


class SystemNotificationDefaultRepository:
    """CRUD for system-wide notification defaults (admin-editable).

    Reads for a given ``notification_type`` are cached in Redis because system
    defaults rarely change. The cache stores the minimal columns needed by the
    cascade (:class:`SystemDefaultRow`) and any mutation (upsert / delete)
    invalidates the key for that stream.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_all(self) -> list[SystemNotificationDefault]:
        stmt = select(SystemNotificationDefault).order_by(
            SystemNotificationDefault.notification_type,
            SystemNotificationDefault.event,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_type(self, notification_type: NotificationType) -> list[SystemDefaultRow]:
        """Return system defaults for a stream, preferring the Redis cache."""
        cached = await self._get_cached(notification_type)
        if cached is not None:
            return cached

        stmt = (
            select(SystemNotificationDefault)
            .where(SystemNotificationDefault.notification_type == notification_type.value)
            .order_by(SystemNotificationDefault.event)
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        views = [_sys_default_to_view(r) for r in rows]
        await self._set_cached(notification_type, rows)
        return views

    async def get_for_event(
        self,
        notification_type: NotificationType,
        event: NotificationEvent,
    ) -> SystemDefaultRow | None:
        """Return a single system default, served from the cached stream list when possible."""
        views = await self.get_by_type(notification_type)
        for v in views:
            if v.event == event.value:
                return v
        return None

    async def upsert(
        self,
        *,
        notification_type: NotificationType,
        event: NotificationEvent,
        values: dict,
    ) -> SystemNotificationDefault:
        """Upsert only the columns present in ``values``.

        ``values`` may contain any subset of ``email_enabled``, ``sms_enabled``,
        ``email_template_id``, ``sms_template_id``. System defaults require both
        booleans at first insert (NOT NULL); when a boolean is missing we seed
        it from the hardcoded per-event default so a template-only upsert can't
        silently enable a channel that should stay off.
        """
        now = datetime.now(UTC)
        email_default = get_event_channel_default(event, notification_type, NotificationChannel.EMAIL)
        sms_default = get_event_channel_default(event, notification_type, NotificationChannel.SMS)
        insert_values = {
            "id": str(uuid4()),
            "notification_type": notification_type.value,
            "event": event.value,
            "email_enabled": values.get("email_enabled", email_default),
            "sms_enabled": values.get("sms_enabled", sms_default),
            "email_template_id": values.get("email_template_id"),
            "sms_template_id": values.get("sms_template_id"),
            "created_at": now,
            "updated_at": now,
            "version": 1,
        }
        set_: dict = {"updated_at": now, "version": SystemNotificationDefault.version + 1}
        for key in ("email_enabled", "sms_enabled", "email_template_id", "sms_template_id"):
            if key in values:
                set_[key] = values[key]
        stmt = (
            pg_insert(SystemNotificationDefault)
            .values(**insert_values)
            .on_conflict_do_update(
                constraint="uq_sys_notif_default_type_event",
                set_=set_,
            )
            .returning(SystemNotificationDefault)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        await self._invalidate(notification_type)
        return result.scalar_one()

    async def delete_all_for_type(self, notification_type: NotificationType) -> int:
        stmt = delete(SystemNotificationDefault).where(
            SystemNotificationDefault.notification_type == notification_type.value,
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        await self._invalidate(notification_type)
        return result.rowcount

    async def delete_all(self) -> int:
        stmt = delete(SystemNotificationDefault)
        result = await self.session.execute(stmt)
        await self.session.flush()
        for nt in NotificationType:
            await self._invalidate(nt)
        return result.rowcount

    @staticmethod
    async def _get_cached(notification_type: NotificationType) -> list[SystemDefaultRow] | None:
        try:
            from app.core.redis import get_redis

            redis = get_redis()
            raw = await redis.get(_sys_defaults_cache_key(notification_type))
            if raw is None:
                return None
            data = json.loads(raw)
            return [_cache_dict_to_view(item) for item in data]
        except Exception:
            logger.debug("notif.sys_defaults_cache_read_failed", notification_type=notification_type.value)
            return None

    @staticmethod
    async def _set_cached(
        notification_type: NotificationType,
        rows: list[SystemNotificationDefault],
    ) -> None:
        try:
            from app.core.redis import get_redis

            redis = get_redis()
            payload = json.dumps([_sys_default_to_cache_dict(r) for r in rows])
            await redis.set(
                _sys_defaults_cache_key(notification_type),
                payload,
                ex=_SYS_DEFAULTS_CACHE_TTL_SECONDS,
            )
        except Exception:
            logger.debug("notif.sys_defaults_cache_write_failed", notification_type=notification_type.value)

    @staticmethod
    async def _invalidate(notification_type: NotificationType) -> None:
        try:
            from app.core.redis import get_redis

            redis = get_redis()
            await redis.delete(_sys_defaults_cache_key(notification_type))
        except Exception:
            logger.debug("notif.sys_defaults_cache_invalidate_failed", notification_type=notification_type.value)


# Organisation overrides repository (layer 3 — nullable columns)


class OrgNotificationPreferenceRepository:
    """CRUD for organisation-level preference overrides."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_organization(
        self,
        organization_id: str,
        *,
        notification_type: NotificationType | None = None,
    ) -> list[OrgNotificationPreference]:
        stmt = select(OrgNotificationPreference).where(OrgNotificationPreference.organization_id == organization_id)
        if notification_type is not None:
            stmt = stmt.where(OrgNotificationPreference.notification_type == notification_type.value)
        stmt = stmt.order_by(OrgNotificationPreference.event)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_event(
        self,
        organization_id: str,
        notification_type: NotificationType,
        event: NotificationEvent,
    ) -> OrgNotificationPreference | None:
        stmt = select(OrgNotificationPreference).where(
            OrgNotificationPreference.organization_id == organization_id,
            OrgNotificationPreference.notification_type == notification_type.value,
            OrgNotificationPreference.event == event.value,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        organization_id: str,
        notification_type: NotificationType,
        event: NotificationEvent,
        values: dict,
    ) -> OrgNotificationPreference:
        """Upsert only the columns present in ``values`` (``None`` explicitly clears)."""
        now = datetime.now(UTC)
        insert_values = {
            "id": str(uuid4()),
            "organization_id": organization_id,
            "notification_type": notification_type.value,
            "event": event.value,
            "email_enabled": values.get("email_enabled"),
            "sms_enabled": values.get("sms_enabled"),
            "email_template_id": values.get("email_template_id"),
            "sms_template_id": values.get("sms_template_id"),
            "created_at": now,
            "updated_at": now,
            "version": 1,
        }
        set_: dict = {"updated_at": now, "version": OrgNotificationPreference.version + 1}
        for key in ("email_enabled", "sms_enabled", "email_template_id", "sms_template_id"):
            if key in values:
                set_[key] = values[key]
        stmt = (
            pg_insert(OrgNotificationPreference)
            .values(**insert_values)
            .on_conflict_do_update(
                constraint="uq_org_notif_pref_org_type_event",
                set_=set_,
            )
            .returning(OrgNotificationPreference)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one()

    async def clear_overrides(
        self,
        *,
        organization_id: str,
        notification_type: NotificationType,
    ) -> int:
        """Null out every override column so the row inherits on every channel."""
        stmt = (
            update(OrgNotificationPreference)
            .where(
                OrgNotificationPreference.organization_id == organization_id,
                OrgNotificationPreference.notification_type == notification_type.value,
            )
            .values(
                email_enabled=None,
                sms_enabled=None,
                email_template_id=None,
                sms_template_id=None,
                updated_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount

    async def delete_all_for_organization(
        self,
        organization_id: str,
        *,
        notification_type: NotificationType | None = None,
    ) -> int:
        stmt = delete(OrgNotificationPreference).where(OrgNotificationPreference.organization_id == organization_id)
        if notification_type is not None:
            stmt = stmt.where(OrgNotificationPreference.notification_type == notification_type.value)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount


# User overrides repository (layer 4 — nullable columns)


class UserNotificationPreferenceRepository:
    """CRUD for per-user preference overrides."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_user(
        self,
        user_id: str,
        *,
        notification_type: NotificationType | None = None,
    ) -> list[UserNotificationPreference]:
        stmt = select(UserNotificationPreference).where(UserNotificationPreference.user_id == user_id)
        if notification_type is not None:
            stmt = stmt.where(UserNotificationPreference.notification_type == notification_type.value)
        stmt = stmt.order_by(UserNotificationPreference.event)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_event(
        self,
        user_id: str,
        notification_type: NotificationType,
        event: NotificationEvent,
    ) -> UserNotificationPreference | None:
        stmt = select(UserNotificationPreference).where(
            UserNotificationPreference.user_id == user_id,
            UserNotificationPreference.notification_type == notification_type.value,
            UserNotificationPreference.event == event.value,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        user_id: str,
        notification_type: NotificationType,
        event: NotificationEvent,
        values: dict,
    ) -> UserNotificationPreference:
        now = datetime.now(UTC)
        insert_values = {
            "id": str(uuid4()),
            "user_id": user_id,
            "notification_type": notification_type.value,
            "event": event.value,
            "email_enabled": values.get("email_enabled"),
            "sms_enabled": values.get("sms_enabled"),
            "email_template_id": values.get("email_template_id"),
            "sms_template_id": values.get("sms_template_id"),
            "created_at": now,
            "updated_at": now,
            "version": 1,
        }
        set_: dict = {"updated_at": now, "version": UserNotificationPreference.version + 1}
        for key in ("email_enabled", "sms_enabled", "email_template_id", "sms_template_id"):
            if key in values:
                set_[key] = values[key]
        stmt = (
            pg_insert(UserNotificationPreference)
            .values(**insert_values)
            .on_conflict_do_update(
                constraint="uq_user_notif_pref_user_type_event",
                set_=set_,
            )
            .returning(UserNotificationPreference)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one()

    async def clear_overrides(
        self,
        *,
        user_id: str,
        notification_type: NotificationType,
    ) -> int:
        stmt = (
            update(UserNotificationPreference)
            .where(
                UserNotificationPreference.user_id == user_id,
                UserNotificationPreference.notification_type == notification_type.value,
            )
            .values(
                email_enabled=None,
                sms_enabled=None,
                email_template_id=None,
                sms_template_id=None,
                updated_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount

    async def delete_all_for_user(self, user_id: str, *, notification_type: NotificationType | None = None) -> int:
        stmt = delete(UserNotificationPreference).where(UserNotificationPreference.user_id == user_id)
        if notification_type is not None:
            stmt = stmt.where(UserNotificationPreference.notification_type == notification_type.value)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount


# Template repository


class NotificationTemplateRepository(BaseRepository):
    """CRUD for notification templates."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, NotificationTemplate)

    async def find_by_id(self, template_id: str) -> NotificationTemplate | None:
        return await self.get_by_id(template_id)

    async def find_by_ids(self, template_ids: list[str]) -> dict[str, NotificationTemplate]:
        """Batch-fetch templates by id, returning a ``{id: template}`` map."""
        if not template_ids:
            return {}
        stmt = select(NotificationTemplate).where(NotificationTemplate.id.in_(template_ids))
        result = await self.session.execute(stmt)
        return {tpl.id: tpl for tpl in result.scalars().all()}


# Device token repository


class DeviceTokenRepository(BaseRepository):
    """CRUD for user device tokens (push notification registration)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, UserDeviceToken)

    async def find_by_user(self, user_id: str, active_only: bool = True) -> list[UserDeviceToken]:
        stmt = select(UserDeviceToken).where(UserDeviceToken.user_id == user_id)
        if active_only:
            stmt = stmt.where(UserDeviceToken.is_active.is_(True))
        stmt = stmt.order_by(UserDeviceToken.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_token(self, device_token: str) -> UserDeviceToken | None:
        stmt = select(UserDeviceToken).where(UserDeviceToken.device_token == device_token)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_token(self, user_id: str, device_token: str, platform: str) -> UserDeviceToken:
        now = datetime.now(UTC)
        stmt = (
            pg_insert(UserDeviceToken)
            .values(
                id=str(uuid4()),
                user_id=user_id,
                device_token=device_token,
                platform=platform,
                is_active=True,
                last_used_at=now,
                created_at=now,
                updated_at=now,
                version=1,
            )
            .on_conflict_do_update(
                constraint="uq_user_device_tokens_token",
                set_={
                    "user_id": user_id,
                    "platform": platform,
                    "is_active": True,
                    "last_used_at": now,
                    "updated_at": now,
                    "version": UserDeviceToken.version + 1,
                },
            )
            .returning(UserDeviceToken)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one()

    async def deactivate(self, token_id: str) -> bool:
        stmt = (
            update(UserDeviceToken)
            .where(UserDeviceToken.id == token_id, UserDeviceToken.is_active.is_(True))
            .values(is_active=False, updated_at=datetime.now(UTC))
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0

    async def deactivate_by_token(self, device_token: str) -> bool:
        stmt = (
            update(UserDeviceToken)
            .where(UserDeviceToken.device_token == device_token, UserDeviceToken.is_active.is_(True))
            .values(is_active=False, updated_at=datetime.now(UTC))
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0


# Inbox repository (user-facing notifications)


class NotificationRepository(BaseRepository):
    """CRUD for user-facing inbox notifications."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Notification)

    async def create_notification(
        self,
        *,
        recipient_id: str,
        organization_id: str | None,
        event: str,
        notification_type: str,
        subject: str | None,
        body: str,
        context_json: dict | None = None,
    ) -> Notification:
        entry = Notification(
            recipient_id=recipient_id,
            organization_id=organization_id,
            event=event,
            notification_type=notification_type,
            subject=subject,
            body=body,
            context_json=context_json,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_for_user(
        self,
        user_id: str,
        *,
        page: int = 1,
        size: int = 20,
        unread_only: bool = False,
    ) -> tuple[list[Notification], int]:
        base_where = [Notification.recipient_id == user_id]
        if unread_only:
            base_where.append(Notification.read_at.is_(None))

        count_stmt = select(func.count()).select_from(Notification).where(*base_where)
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        offset = (page - 1) * size
        stmt = select(Notification).where(*base_where).order_by(Notification.created_at.desc()).offset(offset).limit(size)
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())
        return items, total

    async def unread_count(self, user_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(Notification.recipient_id == user_id, Notification.read_at.is_(None))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def mark_read(self, notification_id: str, user_id: str) -> bool:
        now = datetime.now(UTC)
        stmt = (
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.recipient_id == user_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=now, updated_at=now)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0

    async def mark_all_read(self, user_id: str) -> int:
        now = datetime.now(UTC)
        stmt = (
            update(Notification)
            .where(Notification.recipient_id == user_id, Notification.read_at.is_(None))
            .values(read_at=now, updated_at=now)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount

# External channel audit log repository


class NotificationAuditLogRepository:
    """CRUD for external channel delivery tracking (email, sms, push)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_entry(
        self,
        *,
        notification_id: str | None,
        recipient_id: str | None,
        organization_id: str | None,
        event: str,
        notification_type: str,
        recipient_email: str | None = None,
        recipient_phone: str | None = None,
        subject: str | None = None,
        context_json: dict | None = None,
    ) -> NotificationAuditLog:
        entry = NotificationAuditLog(
            notification_id=notification_id,
            recipient_id=recipient_id,
            organization_id=organization_id,
            event=event,
            notification_type=notification_type,
            recipient_email=recipient_email,
            recipient_phone=recipient_phone,
            subject=subject,
            context_json=context_json,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def update_channel_status(
        self,
        log_id: str,
        *,
        channel: str,
        status: str,
        error: str | None = None,
        external_id: str | None = None,
    ) -> None:
        ch = channel.lower()
        values: dict = {
            f"{ch}_status": status,
            "updated_at": datetime.now(UTC),
        }
        if error is not None:
            values[f"{ch}_error"] = error
        if external_id is not None:
            values[f"{ch}_external_id"] = external_id

        stmt = update(NotificationAuditLog).where(NotificationAuditLog.id == log_id).values(**values)
        await self.session.execute(stmt)
        await self.session.flush()
