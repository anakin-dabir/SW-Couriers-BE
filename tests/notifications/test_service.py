"""Tests for NotificationService (worker-facing) and NotificationManagementService (API-facing)."""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import ActivationLinkRequest
from app.modules.notifications.enums import (
    NotificationChannel,
    NotificationEvent,
    NotificationType,
    PreferenceScope,
    TemplateChannel,
)
from app.modules.notifications.models import Notification
from app.modules.notifications.repository import (
    OrgNotificationPreferenceRepository,
    SystemNotificationDefaultRepository,
    UserNotificationPreferenceRepository,
)
from app.modules.notifications.service import NotificationManagementService, NotificationService
from app.modules.notifications.v1.schemas import (
    CategoryGroup,
    ChannelToggle,
    EventPreferenceUpdate,
    EventResolved,
    UpdatePreferencesRequest,
    UpsertTemplateRequest,
)
from app.modules.organizations.enums import OrganizationStatus
from app.modules.organizations.models import Organization


def _service(session: AsyncSession) -> NotificationService:
    return NotificationService(session)


def _mgmt(session: AsyncSession) -> NotificationManagementService:
    return NotificationManagementService(session)


def _flatten(groups: list[CategoryGroup]) -> list[EventResolved]:
    """Collapse category groups into a flat event list for assertions."""
    return [ev for group in groups for ev in group.preferences]


async def _make_org(db_session: AsyncSession) -> Organization:
    ref_suffix = uuid4().hex[:12]
    org = Organization(
        reference=f"T{ref_suffix}"[:20],
        trading_name=f"Test Org {ref_suffix}",
        legal_entity_name=f"Test Org {ref_suffix} Limited",
        industry="OTHER",
        company_size="1-10 employees",
        date_of_incorporation=date(2020, 1, 1),
        companies_house_number=f"CH{ref_suffix[:8]}",
        reg_address_line_1="1 Test Street",
        reg_city="London",
        reg_postcode="EC1A 1BB",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org)
    await db_session.flush()
    return org


class TestWorkerResolveDriver:
    @pytest.mark.asyncio
    async def test_returns_push_and_inapp_channels(self, db_session: AsyncSession) -> None:
        resolved = await _service(db_session).resolve_notification(
            event=NotificationEvent.BOOKING_CONFIRMATION,
            notification_type=NotificationType.DRIVER,
            organization_id="00000000-0000-0000-0000-000000000001",
            user_id=None,
            context={"tracking_number": "SW-12345", "pickup_address": "123 Main St"},
        )
        channels = [r.channel for r in resolved]
        assert NotificationChannel.PUSH in channels
        assert NotificationChannel.IN_APP in channels


class TestWorkerResolveB2BCustomer:
    @pytest.mark.asyncio
    async def test_uses_hardcoded_defaults_when_no_overrides(
        self, db_session: AsyncSession, user_factory
    ) -> None:
        user = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resolved = await _service(db_session).resolve_notification(
            event=NotificationEvent.BOOKING_CONFIRMATION,
            notification_type=NotificationType.B2B_CUSTOMER,
            organization_id=user.organization_id or "00000000-0000-0000-0000-000000000001",
            user_id=user.id,
        )
        channels = [r.channel for r in resolved]
        assert NotificationChannel.EMAIL in channels

    @pytest.mark.asyncio
    async def test_user_override_disables_email(
        self, db_session: AsyncSession, user_factory
    ) -> None:
        user = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        await UserNotificationPreferenceRepository(db_session).upsert(
            user_id=user.id,
            notification_type=NotificationType.B2B_CUSTOMER,
            event=NotificationEvent.BOOKING_CONFIRMATION,
            values={"email_enabled": False},
        )
        await db_session.flush()

        resolved = await _service(db_session).resolve_notification(
            event=NotificationEvent.BOOKING_CONFIRMATION,
            notification_type=NotificationType.B2B_CUSTOMER,
            organization_id=user.organization_id or "00000000-0000-0000-0000-000000000001",
            user_id=user.id,
        )
        channels = [r.channel for r in resolved]
        assert NotificationChannel.EMAIL not in channels


class TestWorkerResolveRecipient:
    @pytest.mark.asyncio
    async def test_org_override_disables_sms(self, db_session: AsyncSession) -> None:
        org = await _make_org(db_session)
        await OrgNotificationPreferenceRepository(db_session).upsert(
            organization_id=org.id,
            notification_type=NotificationType.RECIPIENT,
            event=NotificationEvent.RECIPIENT_OUT_FOR_DELIVERY,
            values={"sms_enabled": False},
        )
        await db_session.flush()

        resolved = await _service(db_session).resolve_notification(
            event=NotificationEvent.RECIPIENT_OUT_FOR_DELIVERY,
            notification_type=NotificationType.RECIPIENT,
            organization_id=org.id,
        )
        channels = [r.channel for r in resolved]
        assert NotificationChannel.SMS not in channels
        assert NotificationChannel.EMAIL in channels


class TestMgmtPreferencesCascade:
    @pytest.mark.asyncio
    async def test_admin_preferences_grouped_by_category(
        self, db_session: AsyncSession, user_factory
    ) -> None:
        user = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        groups = await _mgmt(db_session).get_preferences(
            scope=PreferenceScope.ADMIN,
            stream=NotificationType.ADMIN_INTERNAL,
            user=user,
        )
        assert len(groups) > 0
        for group in groups:
            assert group.category
            assert group.category_display_name
            assert group.preferences
            for ev in group.preferences:
                assert isinstance(ev.email.enabled, bool)
                assert isinstance(ev.email.default, bool)
                assert isinstance(ev.template_customized, bool)

    @pytest.mark.asyncio
    async def test_admin_update_pins_value(self, db_session: AsyncSession, user_factory) -> None:
        user = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        mgmt = _mgmt(db_session)
        await mgmt.update_preferences(
            scope=PreferenceScope.ADMIN,
            stream=NotificationType.ADMIN_INTERNAL,
            data=UpdatePreferencesRequest(
                preferences=[
                    EventPreferenceUpdate(
                        event=NotificationEvent.ADMIN_NEW_ORDER_CREATED.value,
                        email=ChannelToggle(enabled=False),
                    )
                ]
            ),
            user=user,
        )
        await db_session.flush()

        groups = await mgmt.get_preferences(
            scope=PreferenceScope.ADMIN,
            stream=NotificationType.ADMIN_INTERNAL,
            user=user,
        )
        match = next(e for e in _flatten(groups) if e.event == NotificationEvent.ADMIN_NEW_ORDER_CREATED.value)
        assert match.email.enabled is False

    @pytest.mark.asyncio
    async def test_reset_admin_clears_overrides(self, db_session: AsyncSession, user_factory) -> None:
        user = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        mgmt = _mgmt(db_session)
        await mgmt.update_preferences(
            scope=PreferenceScope.ADMIN,
            stream=NotificationType.ADMIN_INTERNAL,
            data=UpdatePreferencesRequest(
                preferences=[
                    EventPreferenceUpdate(
                        event=NotificationEvent.ADMIN_NEW_ORDER_CREATED.value,
                        email=ChannelToggle(enabled=False),
                    )
                ]
            ),
            user=user,
        )
        await db_session.flush()

        await mgmt.reset_preferences(
            scope=PreferenceScope.ADMIN,
            stream=NotificationType.ADMIN_INTERNAL,
            user=user,
        )
        await db_session.flush()

        groups = await mgmt.get_preferences(
            scope=PreferenceScope.ADMIN,
            stream=NotificationType.ADMIN_INTERNAL,
            user=user,
        )
        match = next(e for e in _flatten(groups) if e.event == NotificationEvent.ADMIN_NEW_ORDER_CREATED.value)
        assert match.email.enabled == match.email.default

    @pytest.mark.asyncio
    async def test_system_defaults_reset_removes_rows(self, db_session: AsyncSession, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        mgmt = _mgmt(db_session)
        await SystemNotificationDefaultRepository(db_session).upsert(
            notification_type=NotificationType.B2B_CUSTOMER,
            event=NotificationEvent.BOOKING_CONFIRMATION,
            values={"email_enabled": False},
        )
        await db_session.flush()

        await mgmt.reset_preferences(
            scope=PreferenceScope.ADMIN,
            stream=NotificationType.B2B_CUSTOMER,
            user=admin,
        )
        await db_session.flush()
        rows = await SystemNotificationDefaultRepository(db_session).get_by_type(NotificationType.B2B_CUSTOMER)
        assert rows == []


class TestMgmtInbox:
    @pytest.mark.asyncio
    async def test_activation_link_request_notifications_include_live_request_status(
        self,
        db_session: AsyncSession,
        user_factory,
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        requester = await user_factory(role="ADMIN", status="PENDING_VERIFICATION", email_verified=False)
        request = ActivationLinkRequest(
            requester_user_id=requester.id,
            status="RESOLVED",
            resolved_by_user_id=admin.id,
        )
        db_session.add(request)
        await db_session.flush()
        db_session.add(
            Notification(
                recipient_id=admin.id,
                organization_id=None,
                event=NotificationEvent.ADMIN_ACTIVATION_LINK_REQUESTED.value,
                notification_type=NotificationType.ADMIN_INTERNAL.value,
                subject=None,
                body="Requester asked for a new link.",
                context_json={
                    "activation_link_request_id": request.id,
                    "requester_user_id": requester.id,
                },
            )
        )
        await db_session.flush()

        inbox = await _mgmt(db_session).list_my_notifications(admin.id)

        assert len(inbox.items) == 1
        context = inbox.items[0].context_json
        assert context is not None
        assert context["request_status"] == "RESOLVED"
        assert context["request_resolved_by_user_id"] == admin.id


class TestMgmtTemplates:
    @pytest.mark.asyncio
    async def test_admin_template_upsert_creates_and_get_resolves(
        self, db_session: AsyncSession, user_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        mgmt = _mgmt(db_session)
        await mgmt.upsert_template_by_context(
            scope=PreferenceScope.ADMIN,
            stream=NotificationType.B2B_CUSTOMER,
            event=NotificationEvent.INVOICE_GENERATED,
            channel=TemplateChannel.EMAIL,
            data=UpsertTemplateRequest(subject="Custom Invoice", body="<p>Invoice ready</p>"),
            user=admin,
        )
        await db_session.flush()

        resolved = await mgmt.get_template_by_context(
            scope=PreferenceScope.ADMIN,
            stream=NotificationType.B2B_CUSTOMER,
            event=NotificationEvent.INVOICE_GENERATED,
            channel=TemplateChannel.EMAIL,
            user=admin,
        )
        assert resolved.is_custom is True
        assert resolved.subject == "Custom Invoice"

    @pytest.mark.asyncio
    async def test_template_hardcoded_fallback(self, db_session: AsyncSession, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resolved = await _mgmt(db_session).get_template_by_context(
            scope=PreferenceScope.ADMIN,
            stream=NotificationType.B2B_CUSTOMER,
            event=NotificationEvent.BOOKING_CONFIRMATION,
            channel=TemplateChannel.EMAIL,
            user=admin,
        )
        assert resolved.is_custom is False
        assert resolved.source == "hardcoded"
        assert resolved.body

    @pytest.mark.asyncio
    async def test_reset_preferences_drops_custom_template(
        self, db_session: AsyncSession, user_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        mgmt = _mgmt(db_session)
        await mgmt.upsert_template_by_context(
            scope=PreferenceScope.ADMIN,
            stream=NotificationType.B2B_CUSTOMER,
            event=NotificationEvent.PAYMENT_RECEIVED,
            channel=TemplateChannel.EMAIL,
            data=UpsertTemplateRequest(subject="x", body="<p>Payment received</p>"),
            user=admin,
        )
        await db_session.flush()

        await mgmt.reset_preferences(
            scope=PreferenceScope.ADMIN,
            stream=NotificationType.B2B_CUSTOMER,
            user=admin,
        )
        await db_session.flush()

        resolved = await mgmt.get_template_by_context(
            scope=PreferenceScope.ADMIN,
            stream=NotificationType.B2B_CUSTOMER,
            event=NotificationEvent.PAYMENT_RECEIVED,
            channel=TemplateChannel.EMAIL,
            user=admin,
        )
        assert resolved.is_custom is False
