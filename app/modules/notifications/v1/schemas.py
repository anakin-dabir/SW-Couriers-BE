"""Pydantic schemas for the notifications module API.

Preferences responses are a **flat list of category groups** — one section
per :class:`NotificationCategory` with its ``category_display_name`` and an
inner ``preferences`` list (one entry per event). Each event carries per-
channel ``enabled`` / ``default`` booleans plus a single ``template_customized``
flag so the UI can render the whole screen from one request.

The caller scope (admin / organization / b2b_dashboard) and the notification
type are already baked into the URL, so responses don't echo them back.

Update requests use ``bool | None`` semantics: omit a field to leave it
unchanged; ``null`` clears the override so the next cascade layer applies;
any other value pins the value at the current layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.common.schemas import BaseResponseSchema, BaseSchema, PaginationParams
from app.modules.notifications.enums import (
    CHANNELS_BY_TYPE,
    DevicePlatform,
    NotificationChannel,
    NotificationEvent,
    NotificationType,
    PreferenceScope,
    PreferenceStream,
    TemplateChannel,
)

# Shared cascade metadata


class ChannelResolved(BaseSchema):
    """Resolved state of a single channel for one event.

    ``enabled`` is the **effective** value after the cascade (user → org →
    system → hardcoded). ``default`` is the hardcoded code-level default
    for the same event / notification type / channel — useful for showing
    a "reset to default" state in the UI without a second request.
    """

    enabled: bool = Field(description="Effective enabled flag after cascade")
    default: bool = Field(description="Hardcoded default for this event / notification type / channel")


class EventResolved(BaseSchema):
    """Resolved state for one event across its channels."""

    event: str
    event_display_name: str
    email: ChannelResolved
    sms: ChannelResolved
    template_customized: bool = Field(
        description=(
            "True if a custom template is pinned for email or sms anywhere in "
            "the cascade. False means both channels fall back to hardcoded "
            "defaults."
        ),
    )


class CategoryGroup(BaseSchema):
    """A group of events under one category.

    The preferences API returns a list of these — the UI renders one section
    per category using ``category_display_name`` as the heading.
    """

    category: str = Field(description="Enum value, e.g. SHIPMENT, BILLING")
    category_display_name: str = Field(description="Human label, e.g. 'Shipment', 'Billing'")
    preferences: list[EventResolved]


# Update payloads — nullable per-channel toggles


class ChannelToggle(BaseSchema):
    """Partial per-channel update.

    - Omit a field      → leave unchanged
    - ``enabled: null`` → clear the override (inherit from next layer)
    - ``enabled: true``  → pin ON at this layer
    - ``enabled: false`` → pin OFF at this layer
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = Field(default=..., description="Null clears the override")


class EventPreferenceUpdate(BaseSchema):
    """Per-event payload in an update request."""

    model_config = ConfigDict(extra="forbid")

    event: str = Field(description="Notification event value (e.g. BOOKING_CONFIRMATION)")
    email: ChannelToggle | None = Field(default=None, description="Omit to leave email unchanged")
    sms: ChannelToggle | None = Field(default=None, description="Omit to leave sms unchanged")

    @field_validator("event")
    @classmethod
    def _validate_event(cls, v: str) -> str:
        try:
            NotificationEvent(v)
        except ValueError:
            valid = ", ".join(e.value for e in NotificationEvent)
            raise ValueError(f"Invalid event '{v}'. Must be one of: {valid}") from None
        return v

    @model_validator(mode="after")
    def _require_any(self) -> EventPreferenceUpdate:
        if self.email is None and self.sms is None:
            raise ValueError("Provide email and/or sms for each event update")
        return self


class UpdatePreferencesRequest(BaseSchema):
    """Bulk update payload — one entry per event, each with optional channels."""

    preferences: list[EventPreferenceUpdate] = Field(
        min_length=1,
        description="List of event-level preference updates",
    )


# Template schemas


class TemplateResponse(BaseSchema):
    """Resolved template for a context GET.

    The URL already identifies the scope (``/admin``, ``/organization/{id}``,
    ``/b2b_dashboard``), notification type, event, and channel, so the response
    does not echo them back — it focuses on the body that needs rendering.
    """

    subject: str | None = Field(description="Null for SMS templates")
    body: str
    variables: list[str] = Field(
        default_factory=list,
        description=(
            "Available template variables for this event / notification type / "
            "channel. Always sourced from hardcoded defaults — custom templates "
            "cannot change the variable registry."
        ),
    )
    source: str = Field(description="'user' | 'org' | 'system' | 'hardcoded' — cascade layer that supplied the body")
    is_custom: bool = Field(description="True if any DB template is linked in the cascade")


class UpsertTemplateRequest(BaseSchema):
    """Create or update a custom template at the scope in the URL."""

    subject: str | None = Field(default=None, max_length=255)
    body: str = Field(min_length=1)


# Device tokens


class RegisterDeviceRequest(BaseSchema):
    """Register a device token for push notifications."""

    device_token: str = Field(min_length=1, max_length=500)
    platform: str = Field(description="IOS, ANDROID, or WEB")

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, v: str) -> str:
        try:
            DevicePlatform(v)
        except ValueError:
            raise ValueError(f"Invalid platform '{v}'. Must be IOS, ANDROID, or WEB") from None
        return v


class DeviceTokenResponse(BaseResponseSchema):
    """Device token detail in API responses."""

    user_id: str
    device_token: str
    platform: str
    is_active: bool
    last_used_at: datetime | None


# Events listing (helper for preference screens)


class EventMeta(BaseSchema):
    """Minimal (event, display name) entry returned by ``GET /events/{notification_type}``."""

    event: str
    event_display_name: str


# Test notification


class TestNotificationRequest(BaseSchema):
    """Send a test notification for a given (scope, notification_type, event).

    The backend resolves the template from the same cascade used for real
    dispatch (user → org → system → hardcoded), renders it with a dummy
    variable context, and sends to whichever contact fields are provided
    for the requested channels.
    """

    model_config = ConfigDict(extra="forbid")

    scope: str = Field(description="ADMIN, ORGANIZATION, or B2B_DASHBOARD — cascade layer to resolve from")
    notification_type: str = Field(description="ADMIN_INTERNAL, B2B_CUSTOMER, or RECIPIENT")
    event: str = Field(description="Notification event, e.g. BOOKING_CONFIRMATION")
    channels: list[str] = Field(
        min_length=1,
        description="Channels to send the test on — subset of ['EMAIL', 'SMS']",
    )
    email: str | None = Field(default=None, description="Required if channels includes EMAIL")
    phone_number: str | None = Field(default=None, description="Required if channels includes SMS")
    organization_id: str | None = Field(
        default=None,
        description="Required when scope is ORGANIZATION — which org's override to render",
    )

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, v: str) -> str:
        try:
            PreferenceScope(v)
        except ValueError:
            valid = ", ".join(s.value for s in PreferenceScope)
            raise ValueError(f"Invalid scope '{v}'. Must be one of: {valid}") from None
        return v

    @field_validator("notification_type")
    @classmethod
    def _validate_notification_type(cls, v: str) -> str:
        try:
            PreferenceStream(v)
        except ValueError:
            valid = ", ".join(s.value for s in PreferenceStream)
            raise ValueError(f"Invalid notification_type '{v}'. Must be one of: {valid}") from None
        return v

    @field_validator("event")
    @classmethod
    def _validate_event(cls, v: str) -> str:
        try:
            NotificationEvent(v)
        except ValueError:
            raise ValueError(f"Invalid event '{v}'") from None
        return v

    @field_validator("channels")
    @classmethod
    def _validate_channels(cls, v: list[str]) -> list[str]:
        allowed = {TemplateChannel.EMAIL.value, TemplateChannel.SMS.value}
        cleaned: list[str] = []
        seen: set[str] = set()
        for ch in v:
            if ch not in allowed:
                raise ValueError(f"Invalid channel '{ch}'. Test notifications are only supported for EMAIL and SMS")
            if ch not in seen:
                cleaned.append(ch)
                seen.add(ch)
        return cleaned

    @model_validator(mode="after")
    def _require_contact_for_each_channel(self) -> TestNotificationRequest:
        if TemplateChannel.EMAIL.value in self.channels and not (self.email and self.email.strip()):
            raise ValueError("email is required when channels includes EMAIL")
        if TemplateChannel.SMS.value in self.channels and not (self.phone_number and self.phone_number.strip()):
            raise ValueError("phone_number is required when channels includes SMS")
        if self.scope == PreferenceScope.ORGANIZATION.value and not self.organization_id:
            raise ValueError("organization_id is required when scope is ORGANIZATION")
        return self


class TestChannelResult(BaseSchema):
    """Per-channel send outcome for a test notification."""

    channel: str = Field(description="EMAIL or SMS")
    status: str = Field(description="SENT or FAILED")
    error: str | None = Field(default=None, description="Failure reason when status is FAILED")


class TestNotificationResponse(BaseSchema):
    """One ``TestChannelResult`` per requested channel, in request order."""

    results: list[TestChannelResult]


# Inbox (user-facing)


class InboxListParams(PaginationParams):
    """Query params for GET /inbox list."""

    unread_only: bool = Field(default=False, description="Filter to unread only")


class NotificationItem(BaseSchema):
    """Single notification in the user's inbox."""

    id: str
    event: str
    notification_type: str
    subject: str | None
    body: str
    context_json: dict[str, Any] | None = Field(
        default=None,
        description="Optional creation context for deep links (e.g. route_id, stop_id).",
    )
    read_at: datetime | None
    created_at: datetime


class UnreadCountResponse(BaseSchema):
    """Unread notification count for badge display."""

    unread_count: int


# Re-export for external callers (organisations master prefs use NotificationType)
__all__ = [
    "BaseResponseSchema",
    "BaseSchema",
    "CHANNELS_BY_TYPE",
    "CategoryGroup",
    "ChannelResolved",
    "ChannelToggle",
    "DevicePlatform",
    "DeviceTokenResponse",
    "EventMeta",
    "EventPreferenceUpdate",
    "EventResolved",
    "InboxListParams",
    "NotificationChannel",
    "NotificationEvent",
    "NotificationItem",
    "NotificationType",
    "PreferenceScope",
    "PreferenceStream",
    "RegisterDeviceRequest",
    "TemplateChannel",
    "TemplateResponse",
    "TestChannelResult",
    "TestNotificationRequest",
    "TestNotificationResponse",
    "UnreadCountResponse",
    "UpdatePreferencesRequest",
    "UpsertTemplateRequest",
]
