"""Notification services.

Two concerns in this module:

- ``NotificationManagementService``: API-facing — inbox, preferences, templates,
  devices, test send. Drives the cascade read/write logic.
- ``NotificationService``: worker-facing — preference resolution and template
  rendering for ``process_notification_task``.

The preference and template cascade is:

    user → org (only for B2B_CUSTOMER / RECIPIENT) → system → hardcoded

At the override layers (user, org) per-channel columns are nullable. ``NULL``
means "inherit from the next layer". ``system_notification_defaults`` stores
concrete booleans and may pin template ids; the final fallback is the
hardcoded code defaults shipped with the module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import structlog
from fastapi import Request
from jinja2 import BaseLoader, Environment
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import UserRole
from app.common.enums.logger import LogEvent
from app.common.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.common.schemas import PaginatedResponse
from app.common.service import BaseService
from app.modules.auth.repository import ActivationLinkRequestRepository
from app.modules.notifications.defaults import (
    get_event_channel_default,
    get_hardcoded_for_context,
    get_hardcoded_variables,
)
from app.modules.notifications.enums import (
    CHANNELS_BY_TYPE,
    EVENT_CATEGORIES,
    EVENT_DISPLAY_NAMES,
    EVENT_NOTIFICATION_STREAMS,
    NotificationCategory,
    NotificationChannel,
    NotificationEvent,
    NotificationType,
    PreferenceScope,
    TemplateChannel,
    category_display_name,
    events_for_notification_type,
)
from app.modules.notifications.models import (
    NotificationTemplate,
    OrgNotificationPreference,
    SystemNotificationDefault,
    UserNotificationPreference,
)
from app.modules.notifications.repository import (
    DeviceTokenRepository,
    NotificationRepository,
    NotificationTemplateRepository,
    OrgNotificationPreferenceRepository,
    SystemNotificationDefaultRepository,
    UserNotificationPreferenceRepository,
)
from app.modules.notifications.types import SystemDefaultRow

if TYPE_CHECKING:
    from app.common.deps import AuthUser
    from app.modules.notifications.v1.schemas import (
        CategoryGroup,
        DeviceTokenResponse,
        EventMeta,
        NotificationItem,
        RegisterDeviceRequest,
        TemplateResponse,
        TestNotificationRequest,
        TestNotificationResponse,
        UnreadCountResponse,
        UpdatePreferencesRequest,
        UpsertTemplateRequest,
    )


logger = structlog.get_logger()
_jinja_env = Environment(loader=BaseLoader(), autoescape=True)


_StorageLayer = Literal["user", "org", "system"]


def _layer_for(scope: PreferenceScope, stream: NotificationType) -> _StorageLayer:
    """Map the caller-facing scope + notification type to its storage layer.

    - ``ADMIN`` + ``ADMIN_INTERNAL``            → ``user`` (admin's own prefs)
    - ``ADMIN`` + ``B2B_CUSTOMER``/``RECIPIENT`` → ``system`` (global defaults)
    - ``ORGANIZATION`` + *                      → ``org``
    - ``B2B_DASHBOARD`` + ``B2B_CUSTOMER``      → ``user`` (b2b contact's prefs)
    """
    if scope == PreferenceScope.ADMIN:
        return "user" if stream == NotificationType.ADMIN_INTERNAL else "system"
    if scope == PreferenceScope.ORGANIZATION:
        return "org"
    return "user"


# Cascade resolution dataclasses


@dataclass(slots=True)
class _Channels:
    """Per-channel (email_enabled, sms_enabled) with nullable channels."""

    email_enabled: bool | None = None
    sms_enabled: bool | None = None
    email_template_id: str | None = None
    sms_template_id: str | None = None


@dataclass(slots=True)
class ResolvedChannelState:
    """Final resolved state for one channel after walking the cascade."""

    enabled: bool
    enabled_source: str
    is_enabled_overridden_here: bool
    template_id: str | None
    template_source: str
    is_template_overridden_here: bool
    has_custom_template: bool


@dataclass(slots=True)
class ResolvedEvent:
    """Resolved state for one event across its channels."""

    event: NotificationEvent
    email: ResolvedChannelState
    sms: ResolvedChannelState


@dataclass(frozen=True, slots=True)
class ResolvedChannel:
    """Send instruction produced by the worker resolver."""

    channel: NotificationChannel
    subject: str
    body: str
    template_name: str | None
    template_id: str | None


@dataclass(slots=True)
class _Cascade:
    """Raw per-channel cascade inputs for an event (any layer may be ``None``)."""

    user: _Channels = field(default_factory=_Channels)
    org: _Channels = field(default_factory=_Channels)
    system: _Channels = field(default_factory=_Channels)


# Management service


class NotificationManagementService(BaseService):
    """API-facing service for notification preferences, templates, inbox, devices."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._session = session
        self._notif_repo = NotificationRepository(session)
        self._activation_link_request_repo = ActivationLinkRequestRepository(session)
        self._sys_repo = SystemNotificationDefaultRepository(session)
        self._user_pref_repo = UserNotificationPreferenceRepository(session)
        self._org_pref_repo = OrgNotificationPreferenceRepository(session)
        self._template_repo = NotificationTemplateRepository(session)
        self._device_repo = DeviceTokenRepository(session)

    # Inbox

    async def list_my_notifications(
        self,
        user_id: str,
        *,
        page: int = 1,
        size: int = 20,
        unread_only: bool = False,
    ) -> PaginatedResponse[NotificationItem]:
        from app.modules.notifications.v1.schemas import NotificationItem

        items, total = await self._notif_repo.list_for_user(user_id, page=page, size=size, unread_only=unread_only)
        activation_request_ids = list(
            {
                str(n.context_json["activation_link_request_id"])
                for n in items
                if n.event == NotificationEvent.ADMIN_ACTIVATION_LINK_REQUESTED.value
                and n.context_json
                and n.context_json.get("activation_link_request_id")
            }
        )
        activation_requests = await self._activation_link_request_repo.get_by_ids(activation_request_ids)

        def _inbox_context(notification) -> dict | None:
            if notification.context_json is None:
                return None
            context = dict(notification.context_json)
            if notification.event != NotificationEvent.ADMIN_ACTIVATION_LINK_REQUESTED.value:
                return context
            request_id = context.get("activation_link_request_id")
            request = activation_requests.get(str(request_id)) if request_id else None
            if request is not None:
                context["request_status"] = request.status
                context["request_resolved_at"] = request.resolved_at
                context["request_resolved_by_user_id"] = request.resolved_by_user_id
                context["request_resolved_invite_id"] = request.resolved_invite_id
            return context

        return PaginatedResponse.create(
            items=[
                NotificationItem(
                    id=n.id,
                    event=n.event,
                    notification_type=n.notification_type,
                    subject=n.subject,
                    body=n.body,
                    context_json=_inbox_context(n),
                    read_at=n.read_at,
                    created_at=n.created_at,
                )
                for n in items
            ],
            total=total,
            page=page,
            size=size,
        )

    async def get_unread_count(self, user_id: str) -> UnreadCountResponse:
        from app.modules.notifications.v1.schemas import UnreadCountResponse

        count = await self._notif_repo.unread_count(user_id)
        return UnreadCountResponse(unread_count=count)

    async def mark_notification_read(self, notification_id: str, user_id: str) -> bool:
        return await self._notif_repo.mark_read(notification_id, user_id)

    async def mark_all_notifications_read(self, user_id: str) -> int:
        return await self._notif_repo.mark_all_read(user_id)

    # Preferences — scoped GET / UPDATE / RESET

    async def get_preferences(
        self,
        *,
        scope: PreferenceScope,
        stream: NotificationType,
        user: AuthUser | None = None,
        user_id: str | None = None,
        organization_id: str | None = None,
    ) -> list[CategoryGroup]:
        """Resolve every event for this (scope, notification type) and group by category.

        Returns one :class:`CategoryGroup` per ``NotificationCategory`` that has
        at least one event for this notification type, preserving enum order
        for both categories (first-seen in event order) and events (enum order).
        Caller-facing scope metadata is *not* echoed back — the URL already
        identifies it.
        """
        from app.modules.notifications.v1.schemas import (
            CategoryGroup,
            ChannelResolved,
            EventResolved,
        )

        self._validate_scope_stream(scope, stream)
        uid, oid = self._resolve_scope_ids(scope, stream, user, user_id, organization_id)

        events = events_for_notification_type(stream)
        resolved_events = await self._resolve_events_for_scope(
            scope=scope, stream=stream, user_id=uid, organization_id=oid, events=events
        )

        grouped: dict[str, tuple[NotificationCategory | None, list[EventResolved]]] = {}
        for ev, resolved in zip(events, resolved_events, strict=True):
            category = EVENT_CATEGORIES.get(ev)
            category_key = category.value if category else "OTHER"
            email_default = get_event_channel_default(ev, stream, NotificationChannel.EMAIL)
            sms_default = get_event_channel_default(ev, stream, NotificationChannel.SMS)
            event_schema = EventResolved(
                event=ev.value,
                event_display_name=EVENT_DISPLAY_NAMES.get(ev, ev.value),
                email=ChannelResolved(enabled=resolved.email.enabled, default=email_default),
                sms=ChannelResolved(enabled=resolved.sms.enabled, default=sms_default),
                template_customized=(
                    resolved.email.is_template_overridden_here
                    or resolved.sms.is_template_overridden_here
                ),
            )
            bucket = grouped.setdefault(category_key, (category, []))
            bucket[1].append(event_schema)

        return [
            CategoryGroup(
                category=key,
                category_display_name=category_display_name(cat),
                preferences=prefs,
            )
            for key, (cat, prefs) in grouped.items()
        ]

    async def update_preferences(
        self,
        *,
        scope: PreferenceScope,
        stream: NotificationType,
        data: UpdatePreferencesRequest,
        user: AuthUser | None = None,
        user_id: str | None = None,
        organization_id: str | None = None,
    ) -> None:
        self._validate_scope_stream(scope, stream)
        uid, oid = self._resolve_scope_ids(scope, stream, user, user_id, organization_id)

        for pref in data.preferences:
            event = NotificationEvent(pref.event)
            values: dict = {}
            if pref.email is not None:
                values["email_enabled"] = pref.email.enabled
            if pref.sms is not None:
                values["sms_enabled"] = pref.sms.enabled
            if not values:
                continue
            await self._upsert_at_scope(
                scope=scope,
                stream=stream,
                event=event,
                values=values,
                user_id=uid,
                organization_id=oid,
            )

        logger.info(
            "notification.preferences_updated",
            scope=scope.value,
            stream=stream.value,
            user_id=uid,
            organization_id=oid,
            count=len(data.preferences),
        )

    async def reset_preferences(
        self,
        *,
        scope: PreferenceScope,
        stream: NotificationType,
        user: AuthUser | None = None,
        user_id: str | None = None,
        organization_id: str | None = None,
    ) -> None:
        """Reset this layer for the given notification type.

        Deletes every override at this layer — both the enabled toggles AND
        any custom templates pinned here — so the next layer down takes over
        for every event and channel. Templates no longer referenced anywhere
        after the reset are hard-deleted.

        * ``me`` / ``org`` → per-scope preference rows for this notification
          type are deleted.
        * ``system`` → system rows for this notification type are deleted so
          the hardcoded defaults apply again.
        """
        self._validate_scope_stream(scope, stream)
        uid, oid = self._resolve_scope_ids(scope, stream, user, user_id, organization_id)
        layer = _layer_for(scope, stream)

        pinned_template_ids = await self._collect_pinned_template_ids_at_scope(
            scope=scope, stream=stream, user_id=uid, organization_id=oid
        )

        if layer == "user":
            assert uid is not None
            await self._user_pref_repo.delete_all_for_user(uid, notification_type=stream)
        elif layer == "org":
            assert oid is not None
            await self._org_pref_repo.delete_all_for_organization(oid, notification_type=stream)
        else:
            await self._sys_repo.delete_all_for_type(stream)

        orphaned = 0
        for tid in pinned_template_ids:
            if not await self._template_still_referenced(tid):
                await self._template_repo.hard_delete(tid)
                orphaned += 1

        logger.info(
            "notification.preferences_reset",
            scope=scope.value,
            stream=stream.value,
            user_id=uid,
            organization_id=oid,
            templates_cleaned=orphaned,
        )

    # Templates — scoped GET / UPSERT / RESET by context

    async def get_template_by_context(
        self,
        *,
        scope: PreferenceScope,
        stream: NotificationType,
        event: NotificationEvent,
        channel: TemplateChannel,
        user: AuthUser | None = None,
        user_id: str | None = None,
        organization_id: str | None = None,
    ) -> TemplateResponse:
        from app.modules.notifications.sanitizers import plain_text_to_html
        from app.modules.notifications.v1.schemas import TemplateResponse

        self._validate_scope_stream(scope, stream)
        uid, oid = self._resolve_scope_ids(scope, stream, user, user_id, organization_id)
        ch = NotificationChannel(channel.value)

        cascade = await self._load_cascade_for_event(
            scope=scope, stream=stream, event=event, user_id=uid, organization_id=oid
        )
        resolved_tid, source = self._resolve_template_id(cascade, ch, scope, stream)

        tpl_model: NotificationTemplate | None = None
        if resolved_tid:
            tpl_model = await self._template_repo.find_by_id(resolved_tid)

        # `is_custom` reflects whether the template is pinned at the *current*
        # scope's layer, not anywhere in the cascade — otherwise a reset at the
        # current scope still surfaces as customized when a deeper layer has one.
        here_layer = _layer_for(scope, stream)
        current_chans = self._current_layer_channels(cascade, here_layer)
        tpl_attr = "email_template_id" if ch == NotificationChannel.EMAIL else "sms_template_id"
        is_custom = getattr(current_chans, tpl_attr) is not None

        subject: str | None
        body: str

        if tpl_model:
            subject = tpl_model.subject
            body = plain_text_to_html(tpl_model.body) if ch == NotificationChannel.EMAIL else tpl_model.body
            template_source = source
        else:
            hardcoded = get_hardcoded_for_context(event.value, stream.value, ch.value)
            if hardcoded:
                subject = hardcoded.get("subject") or None
                body = plain_text_to_html(hardcoded["body"]) if ch == NotificationChannel.EMAIL else hardcoded["body"]
            else:
                fallback = event.value.replace("_", " ").title()
                subject = fallback
                body = fallback
            template_source = "hardcoded"

        return TemplateResponse(
            subject=subject,
            body=body,
            variables=get_hardcoded_variables(event.value, stream.value, ch.value),
            source=template_source,
            is_custom=is_custom,
        )

    async def upsert_template_by_context(
        self,
        *,
        scope: PreferenceScope,
        stream: NotificationType,
        event: NotificationEvent,
        channel: TemplateChannel,
        data: UpsertTemplateRequest,
        user: AuthUser | None = None,
        user_id: str | None = None,
        organization_id: str | None = None,
    ) -> TemplateResponse:
        """Pin a custom template at the given scope/stream/event/channel.

        If a template is already pinned at this layer, it is updated in place.
        Otherwise a new ``notification_templates`` row is created and linked
        via the ``*_template_id`` column on the layer's preference row.
        """
        from app.modules.notifications.sanitizers import sanitize_email_html, strip_html_to_text
        from app.modules.notifications.v1.schemas import TemplateResponse

        self._validate_scope_stream(scope, stream)
        uid, oid = self._resolve_scope_ids(scope, stream, user, user_id, organization_id)
        ch = NotificationChannel(channel.value)

        body = sanitize_email_html(data.body) if ch == NotificationChannel.EMAIL else strip_html_to_text(data.body)

        existing_tid = await self._get_pinned_template_id(
            scope=scope, stream=stream, event=event, channel=ch, user_id=uid, organization_id=oid
        )

        if existing_tid:
            tpl = await self._template_repo.find_by_id(existing_tid)
            if tpl:
                await self._template_repo.update_by_id(
                    tpl.id,
                    {"subject": data.subject, "body": body},
                    expected_version=tpl.version,
                )
                tpl_id = tpl.id
            else:
                tpl_id = None
        else:
            tpl_id = None

        if tpl_id is None:
            tpl_name = self._build_template_name(scope, stream, event, ch, uid, oid)
            template = await self._template_repo.create(
                {
                    "name": tpl_name,
                    "channel": ch.value,
                    "subject": data.subject,
                    "body": body,
                    "is_active": True,
                }
            )
            tpl_id = template.id
            await self._link_template_at_scope(
                scope=scope,
                stream=stream,
                event=event,
                channel=ch,
                template_id=tpl_id,
                user_id=uid,
                organization_id=oid,
            )

        logger.info(
            "notification.template_upserted",
            scope=scope.value,
            stream=stream.value,
            notif_event=event.value,
            channel=ch.value,
            template_id=tpl_id,
        )

        return TemplateResponse(
            subject=data.subject,
            body=body,
            variables=get_hardcoded_variables(event.value, stream.value, ch.value),
            source=_layer_for(scope, stream),
            is_custom=True,
        )

    async def reset_template_by_context(
        self,
        *,
        scope: PreferenceScope,
        stream: NotificationType,
        event: NotificationEvent,
        channel: TemplateChannel,
        user: AuthUser | None = None,
        user_id: str | None = None,
        organization_id: str | None = None,
    ) -> TemplateResponse:
        self._validate_scope_stream(scope, stream)
        uid, oid = self._resolve_scope_ids(scope, stream, user, user_id, organization_id)
        ch = NotificationChannel(channel.value)

        old_tid = await self._get_pinned_template_id(
            scope=scope,
            stream=stream,
            event=event,
            channel=ch,
            user_id=uid,
            organization_id=oid,
        )
        if old_tid is None:
            return await self.get_template_by_context(
                scope=scope,
                stream=stream,
                event=event,
                channel=channel,
                user=user,
                user_id=user_id,
                organization_id=organization_id,
            )

        await self._link_template_at_scope(
            scope=scope,
            stream=stream,
            event=event,
            channel=ch,
            template_id=None,
            user_id=uid,
            organization_id=oid,
        )
        if not await self._template_still_referenced(old_tid):
            await self._template_repo.hard_delete(old_tid)

        logger.info(
            "notification.template_reset",
            scope=scope.value,
            stream=stream.value,
            notif_event=event.value,
            channel=ch.value,
            previous_template_id=old_tid,
        )
        return await self.get_template_by_context(
            scope=scope,
            stream=stream,
            event=event,
            channel=channel,
            user=user,
            user_id=user_id,
            organization_id=organization_id,
        )

    # Devices

    async def register_device(self, user_id: str, data: RegisterDeviceRequest) -> DeviceTokenResponse:
        from app.modules.notifications.v1.schemas import DeviceTokenResponse

        token = await self._device_repo.upsert_token(
            user_id=user_id,
            device_token=data.device_token,
            platform=data.platform,
        )
        return DeviceTokenResponse.model_validate(token)

    async def unregister_device(self, token_id: str, user_id: str) -> None:
        token = await self._device_repo.get_by_id(token_id)
        if token is None or token.user_id != user_id:
            raise NotFoundError(resource="user_device_tokens", id=token_id)
        await self._device_repo.deactivate(token_id)

    # Events listing — helper for preference screens

    async def list_events_for_type(self, notification_type: NotificationType) -> list[EventMeta]:
        """All events supported for a given notification type (with display labels)."""
        from app.modules.notifications.v1.schemas import EventMeta

        events = events_for_notification_type(notification_type)
        return [
            EventMeta(event=ev.value, event_display_name=EVENT_DISPLAY_NAMES.get(ev, ev.value))
            for ev in events
        ]

    # Test notification

    async def send_test_notification(
        self,
        data: TestNotificationRequest,
        *,
        user: AuthUser | None = None,
    ) -> TestNotificationResponse:
        """Resolve the configured template for (scope, type, event) and send it.

        Uses the same cascade as real dispatch so the test reflects what the
        caller would actually receive with their current overrides. Template
        variables are substituted with placeholder values sourced from the
        hardcoded variable registry.
        """
        from app.modules.notifications.senders.email import EmailSender
        from app.modules.notifications.senders.sms import SmsSender
        from app.modules.notifications.v1.schemas import TestChannelResult, TestNotificationResponse

        scope = PreferenceScope(data.scope)
        stream = NotificationType(data.notification_type)
        event = NotificationEvent(data.event)

        self._validate_scope_stream(scope, stream)
        if event not in events_for_notification_type(stream):
            raise ValidationError(
                f"Event '{event.value}' is not valid for notification_type '{stream.value}'"
            )

        uid, oid = self._resolve_scope_ids(
            scope, stream, user, None, data.organization_id
        )
        cascade = await self._load_cascade_for_event(
            scope=scope, stream=stream, event=event, user_id=uid, organization_id=oid
        )

        email_sender: EmailSender | None = None
        sms_sender: SmsSender | None = None
        results: list[TestChannelResult] = []

        for raw_ch in data.channels:
            channel = NotificationChannel(raw_ch)
            subject, body = await self._render_test_channel(cascade, stream, event, channel, scope)

            if channel == NotificationChannel.EMAIL:
                assert data.email is not None
                email_sender = email_sender or EmailSender()
                status, error, _ = await email_sender.send(
                    to_address=data.email,
                    subject=subject or event.value.replace("_", " ").title(),
                    body=body,
                )
                results.append(TestChannelResult(channel=channel.value, status=status.value, error=error))
            else:
                assert data.phone_number is not None
                sms_sender = sms_sender or SmsSender()
                status, error, _ = await sms_sender.send(
                    to_number=data.phone_number,
                    body=body,
                )
                results.append(TestChannelResult(channel=channel.value, status=status.value, error=error))

        logger.info(
            "notification.test_sent",
            scope=scope.value,
            notification_type=stream.value,
            notif_event=event.value,
            channels=[r.channel for r in results],
            statuses=[r.status for r in results],
        )
        return TestNotificationResponse(results=results)

    async def _render_test_channel(
        self,
        cascade: _Cascade,
        stream: NotificationType,
        event: NotificationEvent,
        channel: NotificationChannel,
        scope: PreferenceScope,
    ) -> tuple[str | None, str]:
        """Resolve cascade → template → render with placeholder context."""
        from app.modules.notifications.sanitizers import plain_text_to_html

        tid, _ = self._resolve_template_id(cascade, channel, scope, stream)
        subject_raw: str | None = None
        body_raw: str = ""
        tpl = await self._template_repo.find_by_id(tid) if tid else None
        if tpl:
            subject_raw = tpl.subject
            body_raw = tpl.body
        else:
            hardcoded = get_hardcoded_for_context(event.value, stream.value, channel.value)
            if hardcoded:
                subject_raw = hardcoded.get("subject") or None
                body_raw = hardcoded["body"]
            else:
                fallback = event.value.replace("_", " ").title()
                subject_raw = fallback
                body_raw = fallback

        variables = get_hardcoded_variables(event.value, stream.value, channel.value)
        context = {var: f"{{{var}}}" for var in variables}

        subject = _jinja_env.from_string(subject_raw).render(**context) if subject_raw else None
        body = _jinja_env.from_string(body_raw).render(**context) if body_raw else body_raw
        if channel == NotificationChannel.EMAIL:
            body = plain_text_to_html(body)
        return subject, body

    # Cascade helpers — shared by preferences and templates

    def _validate_scope_stream(self, scope: PreferenceScope, stream: NotificationType) -> None:
        """Enforce the allowed (scope, notification type) pairs for each API prefix."""
        if scope == PreferenceScope.ADMIN:
            allowed = (NotificationType.ADMIN_INTERNAL, NotificationType.B2B_CUSTOMER, NotificationType.RECIPIENT)
            if stream not in allowed:
                raise ValidationError(
                    "/admin route supports ADMIN_INTERNAL, B2B_CUSTOMER, or RECIPIENT notification types"
                )
        elif scope == PreferenceScope.ORGANIZATION:
            if stream not in (NotificationType.B2B_CUSTOMER, NotificationType.RECIPIENT):
                raise ValidationError(
                    "/organization route supports B2B_CUSTOMER or RECIPIENT notification types"
                )
        elif scope == PreferenceScope.B2B_DASHBOARD:
            if stream != NotificationType.B2B_CUSTOMER:
                raise ValidationError(
                    "/b2b_dashboard route supports only the B2B_CUSTOMER notification type"
                )

    def _resolve_scope_ids(
        self,
        scope: PreferenceScope,
        stream: NotificationType,
        user: AuthUser | None,
        user_id: str | None,
        organization_id: str | None,
    ) -> tuple[str | None, str | None]:
        """Figure out which (user_id, org_id) pair applies to a scope/layer."""
        uid = user_id or (user.id if user else None)
        oid = organization_id
        layer = _layer_for(scope, stream)

        if layer == "user":
            if uid is None:
                raise ValidationError("authenticated user required for this scope")
            if stream == NotificationType.B2B_CUSTOMER and oid is None and user is not None:
                oid = getattr(user, "organization_id", None)
        elif layer == "org":
            if oid is None:
                raise ValidationError("organization_id required for organization scope")
            self._check_org_access(user, oid)
        return uid, oid

    def _check_org_access(self, user: AuthUser | None, organization_id: str) -> None:
        if user is None:
            return
        if user.role == UserRole.CUSTOMER_B2B:
            user_org_id = getattr(user, "organization_id", None)
            if not user_org_id or user_org_id != organization_id:
                raise ForbiddenError("You can only manage preferences for your own organization")

    async def _resolve_events_for_scope(
        self,
        *,
        scope: PreferenceScope,
        stream: NotificationType,
        user_id: str | None,
        organization_id: str | None,
        events: tuple[NotificationEvent, ...],
    ) -> list[ResolvedEvent]:
        """Fetch all three layers for a scope in bulk and resolve per event."""
        user_rows: list[UserNotificationPreference] = []
        org_rows: list[OrgNotificationPreference] = []
        sys_rows: list[SystemDefaultRow] = []

        if user_id and stream in (NotificationType.ADMIN_INTERNAL, NotificationType.B2B_CUSTOMER):
            user_rows = await self._user_pref_repo.get_for_user(user_id, notification_type=stream)

        if organization_id and stream in (NotificationType.B2B_CUSTOMER, NotificationType.RECIPIENT):
            org_rows = await self._org_pref_repo.get_for_organization(organization_id, notification_type=stream)

        if stream in (NotificationType.B2B_CUSTOMER, NotificationType.RECIPIENT):
            sys_rows = await self._sys_repo.get_by_type(stream)

        user_map = {r.event: r for r in user_rows}
        org_map = {r.event: r for r in org_rows}
        sys_map = {r.event: r for r in sys_rows}

        resolved: list[ResolvedEvent] = []
        for ev in events:
            cascade = _Cascade(
                user=self._row_to_channels(user_map.get(ev.value)),
                org=self._row_to_channels(org_map.get(ev.value)),
                system=self._row_to_channels(sys_map.get(ev.value)),
            )
            resolved.append(
                ResolvedEvent(
                    event=ev,
                    email=self._resolve_channel(cascade, NotificationChannel.EMAIL, scope, stream, ev),
                    sms=self._resolve_channel(cascade, NotificationChannel.SMS, scope, stream, ev),
                )
            )
        return resolved

    async def _load_cascade_for_event(
        self,
        *,
        scope: PreferenceScope,
        stream: NotificationType,
        event: NotificationEvent,
        user_id: str | None,
        organization_id: str | None,
    ) -> _Cascade:
        """Point-read the cascade for a single event (used for template lookups)."""
        cascade = _Cascade()
        if user_id and stream in (NotificationType.ADMIN_INTERNAL, NotificationType.B2B_CUSTOMER):
            cascade.user = self._row_to_channels(
                await self._user_pref_repo.get_for_event(user_id, stream, event)
            )
        if organization_id and stream in (NotificationType.B2B_CUSTOMER, NotificationType.RECIPIENT):
            cascade.org = self._row_to_channels(
                await self._org_pref_repo.get_for_event(organization_id, stream, event)
            )
        if stream in (NotificationType.B2B_CUSTOMER, NotificationType.RECIPIENT):
            cascade.system = self._row_to_channels(await self._sys_repo.get_for_event(stream, event))
        return cascade

    @staticmethod
    def _row_to_channels(
        row: UserNotificationPreference | OrgNotificationPreference | SystemDefaultRow | None,
    ) -> _Channels:
        if row is None:
            return _Channels()
        return _Channels(
            email_enabled=row.email_enabled,
            sms_enabled=row.sms_enabled,
            email_template_id=row.email_template_id,
            sms_template_id=row.sms_template_id,
        )

    def _resolve_channel(
        self,
        cascade: _Cascade,
        channel: NotificationChannel,
        scope: PreferenceScope,
        stream: NotificationType,
        event: NotificationEvent,
    ) -> ResolvedChannelState:
        """Walk user → org → system → hardcoded for one channel."""
        here_layer = _layer_for(scope, stream)
        attr = "email_enabled" if channel == NotificationChannel.EMAIL else "sms_enabled"
        tpl_attr = "email_template_id" if channel == NotificationChannel.EMAIL else "sms_template_id"

        enabled: bool | None = None
        enabled_source = "hardcoded"
        template_id: str | None = None
        template_source = "hardcoded"

        for layer_name, chans in (("user", cascade.user), ("org", cascade.org), ("system", cascade.system)):
            value = getattr(chans, attr)
            if enabled is None and value is not None:
                enabled = value
                enabled_source = layer_name
            tid = getattr(chans, tpl_attr)
            if template_id is None and tid is not None:
                template_id = tid
                template_source = layer_name

        if enabled is None:
            enabled = get_event_channel_default(event, stream, channel)
            enabled_source = "hardcoded"

        current_layer = self._current_layer_channels(cascade, here_layer)
        is_enabled_here = getattr(current_layer, attr) is not None
        is_template_here = getattr(current_layer, tpl_attr) is not None

        return ResolvedChannelState(
            enabled=enabled,
            enabled_source=enabled_source,
            is_enabled_overridden_here=is_enabled_here and enabled_source == here_layer,
            template_id=template_id,
            template_source=template_source,
            is_template_overridden_here=is_template_here and template_source == here_layer,
            has_custom_template=template_id is not None,
        )

    @staticmethod
    def _current_layer_channels(cascade: _Cascade, layer: _StorageLayer) -> _Channels:
        if layer == "user":
            return cascade.user
        if layer == "org":
            return cascade.org
        return cascade.system

    async def _upsert_at_scope(
        self,
        *,
        scope: PreferenceScope,
        stream: NotificationType,
        event: NotificationEvent,
        values: dict,
        user_id: str | None,
        organization_id: str | None,
    ) -> None:
        layer = _layer_for(scope, stream)
        if layer == "user":
            assert user_id is not None
            await self._user_pref_repo.upsert(
                user_id=user_id,
                notification_type=stream,
                event=event,
                values=values,
            )
        elif layer == "org":
            assert organization_id is not None
            await self._org_pref_repo.upsert(
                organization_id=organization_id,
                notification_type=stream,
                event=event,
                values=values,
            )
        else:
            await self._sys_repo.upsert(
                notification_type=stream,
                event=event,
                values=values,
            )

    async def _get_pinned_template_id(
        self,
        *,
        scope: PreferenceScope,
        stream: NotificationType,
        event: NotificationEvent,
        channel: NotificationChannel,
        user_id: str | None,
        organization_id: str | None,
    ) -> str | None:
        """Read the template id at exactly one layer (not cascaded)."""
        tpl_attr = "email_template_id" if channel == NotificationChannel.EMAIL else "sms_template_id"
        layer = _layer_for(scope, stream)
        if layer == "user":
            assert user_id is not None
            row = await self._user_pref_repo.get_for_event(user_id, stream, event)
        elif layer == "org":
            assert organization_id is not None
            row = await self._org_pref_repo.get_for_event(organization_id, stream, event)
        else:
            row = await self._sys_repo.get_for_event(stream, event)
        return getattr(row, tpl_attr, None) if row else None

    async def _collect_pinned_template_ids_at_scope(
        self,
        *,
        scope: PreferenceScope,
        stream: NotificationType,
        user_id: str | None,
        organization_id: str | None,
    ) -> list[str]:
        """Every non-null email/sms template id currently pinned at this layer."""
        layer = _layer_for(scope, stream)
        if layer == "user":
            assert user_id is not None
            rows = await self._user_pref_repo.get_for_user(user_id, notification_type=stream)
        elif layer == "org":
            assert organization_id is not None
            rows = await self._org_pref_repo.get_for_organization(organization_id, notification_type=stream)
        else:
            rows = await self._sys_repo.get_by_type(stream)

        ids: list[str] = []
        for row in rows:
            email_tid: str | None = getattr(row, "email_template_id", None)
            sms_tid: str | None = getattr(row, "sms_template_id", None)
            if email_tid is not None:
                ids.append(email_tid)
            if sms_tid is not None:
                ids.append(sms_tid)
        return ids

    async def _link_template_at_scope(
        self,
        *,
        scope: PreferenceScope,
        stream: NotificationType,
        event: NotificationEvent,
        channel: NotificationChannel,
        template_id: str | None,
        user_id: str | None,
        organization_id: str | None,
    ) -> None:
        tpl_col = "email_template_id" if channel == NotificationChannel.EMAIL else "sms_template_id"
        await self._upsert_at_scope(
            scope=scope,
            stream=stream,
            event=event,
            values={tpl_col: template_id},
            user_id=user_id,
            organization_id=organization_id,
        )

    def _resolve_template_id(
        self,
        cascade: _Cascade,
        channel: NotificationChannel,
        scope: PreferenceScope,  # noqa: ARG002
        stream: NotificationType,  # noqa: ARG002
    ) -> tuple[str | None, str]:
        """Walk the cascade for a template_id — returns ``(template_id, source)``."""
        tpl_attr = "email_template_id" if channel == NotificationChannel.EMAIL else "sms_template_id"
        for layer_name, chans in [("user", cascade.user), ("org", cascade.org), ("system", cascade.system)]:
            tid = getattr(chans, tpl_attr)
            if tid is not None:
                return tid, layer_name
        return None, "hardcoded"

    @staticmethod
    def _build_template_name(
        scope: PreferenceScope,
        stream: NotificationType,
        event: NotificationEvent,
        channel: NotificationChannel,
        user_id: str | None,
        organization_id: str | None,
    ) -> str:
        layer = _layer_for(scope, stream)
        suffix = ""
        if layer == "user" and user_id:
            suffix = f"_USER_{user_id[:8]}"
        elif layer == "org" and organization_id:
            suffix = f"_ORG_{organization_id[:8]}"
        elif layer == "system":
            suffix = "_SYSTEM"
        return f"{event.value}_{stream.value}_{channel.value}{suffix}"

    async def _template_still_referenced(self, template_id: str) -> bool:
        """True if any preference row still points to this template id."""
        from sqlalchemy import or_, select

        stmts = [
            select(UserNotificationPreference.id).where(
                or_(
                    UserNotificationPreference.email_template_id == template_id,
                    UserNotificationPreference.sms_template_id == template_id,
                )
            ),
            select(OrgNotificationPreference.id).where(
                or_(
                    OrgNotificationPreference.email_template_id == template_id,
                    OrgNotificationPreference.sms_template_id == template_id,
                )
            ),
            select(SystemNotificationDefault.id).where(
                or_(
                    SystemNotificationDefault.email_template_id == template_id,
                    SystemNotificationDefault.sms_template_id == template_id,
                )
            ),
        ]
        for stmt in stmts:
            result = await self._session.execute(stmt.limit(1))
            if result.first() is not None:
                return True
        return False


# Worker resolver


class NotificationService(BaseService):
    """Worker-facing resolver — resolves a single recipient's enabled channels
    and rendered templates for one event in one go."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._mgmt = NotificationManagementService(session, request)

    async def resolve_notification(
        self,
        *,
        event: NotificationEvent,
        notification_type: NotificationType,
        organization_id: str | None = None,
        user_id: str | None = None,
        context: dict | None = None,
    ) -> list[ResolvedChannel]:
        """Resolve all channels for a single recipient.

        Walks the cascade once per channel, loads the pinned template if any,
        and falls back to the hardcoded defaults for every missing piece.
        Returns only the enabled channels with ready-to-send subject/body.
        """
        context = context or {}
        valid_channels = CHANNELS_BY_TYPE.get(notification_type, ())

        if notification_type == NotificationType.DRIVER:
            return self._resolve_driver(event, notification_type, valid_channels, context)

        if not organization_id:
            logger.warning(
                LogEvent.NOTIFICATION_RESOLVE_MISSING_ORGANIZATION,
                notif_event=event.value,
                notification_type=notification_type.value,
            )
            return []

        allowed = EVENT_NOTIFICATION_STREAMS.get(event, frozenset())
        if notification_type not in allowed:
            logger.warning(
                "notification.invalid_event_for_stream",
                notif_event=event.value,
                notification_type=notification_type.value,
            )
            return []

        scope_for_read = PreferenceScope.B2B_DASHBOARD if user_id else PreferenceScope.ORGANIZATION
        cascade = await self._mgmt._load_cascade_for_event(
            scope=scope_for_read,
            stream=notification_type,
            event=event,
            user_id=user_id,
            organization_id=organization_id,
        )

        template_ids: list[str] = []
        for ch in valid_channels:
            tid, _ = self._mgmt._resolve_template_id(cascade, ch, scope_for_read, notification_type)
            if tid:
                template_ids.append(tid)
        templates = await self._mgmt._template_repo.find_by_ids(template_ids) if template_ids else {}

        resolved: list[ResolvedChannel] = []
        mgmt = self._mgmt
        for ch in valid_channels:
            state = mgmt._resolve_channel(cascade, ch, scope_for_read, notification_type, event)
            if not state.enabled:
                continue
            tpl = templates.get(state.template_id) if state.template_id else None
            subject: str
            body: str
            template_name: str | None = None
            template_id: str | None = None
            if tpl:
                subject = tpl.subject or ""
                body = tpl.body
                template_name = tpl.name
                template_id = tpl.id
            else:
                hardcoded = get_hardcoded_for_context(event.value, notification_type.value, ch.value)
                if hardcoded:
                    subject = hardcoded.get("subject") or ""
                    body = hardcoded["body"]
                    template_name = hardcoded["name"]
                else:
                    subject = event.value.replace("_", " ").title()
                    body = f"Notification: {subject}"
            resolved.append(
                ResolvedChannel(
                    channel=ch,
                    subject=self._render_string(subject, context),
                    body=self._render_string(body, context),
                    template_name=template_name,
                    template_id=template_id,
                )
            )

        logger.info(
            LogEvent.NOTIFICATION_RESOLVED,
            notif_event=event.value,
            notification_type=notification_type.value,
            organization_id=organization_id,
            channels=[r.channel.value for r in resolved],
        )
        return resolved

    def _resolve_driver(
        self,
        event: NotificationEvent,
        notification_type: NotificationType,
        valid_channels: tuple[NotificationChannel, ...],
        context: dict,
    ) -> list[ResolvedChannel]:
        resolved: list[ResolvedChannel] = []
        for ch in valid_channels:
            hardcoded = get_hardcoded_for_context(event.value, notification_type.value, ch.value)
            if hardcoded:
                subject = hardcoded.get("subject") or ""
                body = hardcoded["body"]
                template_name = hardcoded["name"]
            else:
                subject = event.value.replace("_", " ").title()
                body = f"Notification: {subject}"
                template_name = None
            resolved.append(
                ResolvedChannel(
                    channel=ch,
                    subject=self._render_string(subject, context),
                    body=self._render_string(body, context),
                    template_name=template_name,
                    template_id=None,
                )
            )
        return resolved

    @staticmethod
    def _render_string(template_str: str, context: dict) -> str:
        if not template_str or "{{" not in template_str:
            return template_str
        try:
            tpl = _jinja_env.from_string(template_str)
            return tpl.render(**context)
        except Exception:
            logger.debug(LogEvent.NOTIFICATION_TEMPLATE_RENDER_FAILED, template=template_str[:50])
            return template_str
