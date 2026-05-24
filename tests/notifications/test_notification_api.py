"""API tests for notification routes — inbox, scoped preferences, scoped templates, devices, test send."""

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token

_PREFIX = "/v1/notifications"


def _admin_headers(user_id: str, role: str = "ADMIN") -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role=role, client_type="ADMIN")
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "ADMIN",
    }


class TestInbox:
    @pytest.mark.asyncio
    async def test_list_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get(f"{_PREFIX}/inbox")
        assert resp.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_list_returns_paginated(self, client: AsyncClient, verified_user, auth_headers) -> None:
        resp = await client.get(f"{_PREFIX}/inbox", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "size" in data

    @pytest.mark.asyncio
    async def test_unread_count(self, client: AsyncClient, verified_user, auth_headers) -> None:
        resp = await client.get(f"{_PREFIX}/inbox/unread/count", headers=auth_headers)
        assert resp.status_code == 200
        assert "unread_count" in resp.json()["data"]

    @pytest.mark.asyncio
    async def test_mark_read_not_found(self, client: AsyncClient, verified_user, auth_headers) -> None:
        fake_id = "00000000-0000-0000-0000-000000000999"
        resp = await client.put(f"{_PREFIX}/inbox/{fake_id}/read", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_mark_all_read(self, client: AsyncClient, verified_user, auth_headers) -> None:
        resp = await client.put(f"{_PREFIX}/inbox/read-all", headers=auth_headers)
        assert resp.status_code == 200


class TestAdminPreferences:
    @pytest.mark.asyncio
    async def test_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get(f"{_PREFIX}/preferences/admin/ADMIN_INTERNAL")
        assert resp.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_admin_internal_returns_grouped_categories(
        self, client: AsyncClient, user_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(f"{_PREFIX}/preferences/admin/ADMIN_INTERNAL", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) > 0
        group = data[0]
        for field in ("category", "category_display_name", "preferences"):
            assert field in group
        assert group["preferences"]
        ev = group["preferences"][0]
        for field in ("event", "event_display_name", "email", "sms", "template_customized"):
            assert field in ev
        assert "category" not in ev
        for field in ("enabled", "default"):
            assert field in ev["email"]
            assert field in ev["sms"]

    @pytest.mark.asyncio
    async def test_b2b_customer_admin_returns_grouped(
        self, client: AsyncClient, user_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(
            f"{_PREFIX}/preferences/admin/B2B_CUSTOMER", headers=_admin_headers(admin.id)
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) > 0
        assert all("category_display_name" in g for g in data)

    @pytest.mark.asyncio
    async def test_non_admin_forbidden(
        self, client: AsyncClient, verified_user, auth_headers
    ) -> None:
        resp = await client.get(f"{_PREFIX}/preferences/admin/ADMIN_INTERNAL", headers=auth_headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_patch_pins_and_reset_admin(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        resp = await client.patch(
            f"{_PREFIX}/preferences/admin/ADMIN_INTERNAL",
            headers=headers,
            json={"preferences": [{"event": "ADMIN_NEW_ORDER_CREATED", "email": {"enabled": False}}]},
        )
        assert resp.status_code == 200

        resp = await client.get(f"{_PREFIX}/preferences/admin/ADMIN_INTERNAL", headers=headers)
        events = [ev for group in resp.json()["data"] for ev in group["preferences"]]
        match = next(e for e in events if e["event"] == "ADMIN_NEW_ORDER_CREATED")
        assert match["email"]["enabled"] is False

        resp = await client.post(f"{_PREFIX}/preferences/admin/ADMIN_INTERNAL/reset", headers=headers)
        assert resp.status_code == 200

        resp = await client.get(f"{_PREFIX}/preferences/admin/ADMIN_INTERNAL", headers=headers)
        events = [ev for group in resp.json()["data"] for ev in group["preferences"]]
        match = next(e for e in events if e["event"] == "ADMIN_NEW_ORDER_CREATED")
        assert match["email"]["enabled"] == match["email"]["default"]

    @pytest.mark.asyncio
    async def test_patch_and_reset_system_defaults(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        resp = await client.patch(
            f"{_PREFIX}/preferences/admin/B2B_CUSTOMER",
            headers=headers,
            json={"preferences": [{"event": "BOOKING_CONFIRMATION", "email": {"enabled": False}}]},
        )
        assert resp.status_code == 200

        resp = await client.post(
            f"{_PREFIX}/preferences/admin/B2B_CUSTOMER/reset", headers=headers
        )
        assert resp.status_code == 200


class TestOrganizationPreferences:
    @pytest.mark.asyncio
    async def test_requires_admin_or_b2b(self, client: AsyncClient, verified_user, auth_headers) -> None:
        org_id = "00000000-0000-0000-0000-000000000001"
        resp = await client.get(f"{_PREFIX}/preferences/organization/{org_id}/RECIPIENT", headers=auth_headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_read(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        org_id = admin.organization_id or "00000000-0000-0000-0000-000000000001"
        resp = await client.get(
            f"{_PREFIX}/preferences/organization/{org_id}/RECIPIENT",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) > 0
        assert all("category" in g and "category_display_name" in g for g in data)

    @pytest.mark.asyncio
    async def test_admin_internal_rejected(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        org_id = admin.organization_id or "00000000-0000-0000-0000-000000000001"
        resp = await client.get(
            f"{_PREFIX}/preferences/organization/{org_id}/ADMIN_INTERNAL",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 422


class TestAdminTemplates:
    @pytest.mark.asyncio
    async def test_requires_admin(
        self, client: AsyncClient, verified_user, auth_headers
    ) -> None:
        resp = await client.get(
            f"{_PREFIX}/templates/admin/B2B_CUSTOMER/BOOKING_CONFIRMATION/EMAIL",
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_get_returns_hardcoded(
        self, client: AsyncClient, user_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(
            f"{_PREFIX}/templates/admin/B2B_CUSTOMER/BOOKING_CONFIRMATION/EMAIL",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["is_custom"] is False
        assert data["body"]

    @pytest.mark.asyncio
    async def test_rejects_invalid_channel(
        self, client: AsyncClient, user_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(
            f"{_PREFIX}/templates/admin/B2B_CUSTOMER/BOOKING_CONFIRMATION/IN_APP",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_upsert_then_get(
        self, client: AsyncClient, user_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        resp = await client.put(
            f"{_PREFIX}/templates/admin/B2B_CUSTOMER/INVOICE_GENERATED/EMAIL",
            headers=headers,
            json={"subject": "Custom Invoice", "body": "<p>Invoice ready</p>"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["is_custom"] is True
        assert data["subject"] == "Custom Invoice"

        resp = await client.get(
            f"{_PREFIX}/templates/admin/B2B_CUSTOMER/INVOICE_GENERATED/EMAIL",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["is_custom"] is True

    @pytest.mark.asyncio
    async def test_preferences_reset_clears_custom_template(
        self, client: AsyncClient, user_factory
    ) -> None:
        """POST /preferences/admin/.../reset wipes both toggles and custom templates."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        await client.put(
            f"{_PREFIX}/templates/admin/B2B_CUSTOMER/PAYMENT_RECEIVED/EMAIL",
            headers=headers,
            json={"subject": "x", "body": "<p>Payment received</p>"},
        )
        resp = await client.get(
            f"{_PREFIX}/templates/admin/B2B_CUSTOMER/PAYMENT_RECEIVED/EMAIL", headers=headers
        )
        assert resp.json()["data"]["is_custom"] is True

        resp = await client.post(
            f"{_PREFIX}/preferences/admin/B2B_CUSTOMER/reset", headers=headers
        )
        assert resp.status_code == 200

        resp = await client.get(
            f"{_PREFIX}/templates/admin/B2B_CUSTOMER/PAYMENT_RECEIVED/EMAIL", headers=headers
        )
        assert resp.json()["data"]["is_custom"] is False


class TestDevices:
    @pytest.mark.asyncio
    async def test_register_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.post(
            f"{_PREFIX}/devices",
            json={"device_token": "test_fcm_token_123", "platform": "ANDROID"},
        )
        assert resp.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_register_returns_201(self, client: AsyncClient, verified_user, auth_headers) -> None:
        resp = await client.post(
            f"{_PREFIX}/devices",
            headers=auth_headers,
            json={"device_token": "test_fcm_token_abc", "platform": "ANDROID"},
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["device_token"] == "test_fcm_token_abc"

    @pytest.mark.asyncio
    async def test_register_invalid_platform(self, client: AsyncClient, verified_user, auth_headers) -> None:
        resp = await client.post(
            f"{_PREFIX}/devices",
            headers=auth_headers,
            json={"device_token": "token", "platform": "WINDOWS"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_unregister_not_found(self, client: AsyncClient, verified_user, auth_headers) -> None:
        resp = await client.delete(
            f"{_PREFIX}/devices/00000000-0000-0000-0000-000000000999",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestSendTest:
    @pytest.mark.asyncio
    async def test_requires_admin_or_b2b(self, client: AsyncClient, verified_user, auth_headers) -> None:
        resp = await client.post(
            f"{_PREFIX}/test",
            headers=auth_headers,
            json={
                "scope": "ADMIN",
                "notification_type": "ADMIN_INTERNAL",
                "event": "ADMIN_NEW_ORDER_CREATED",
                "channels": ["EMAIL"],
                "email": "test@example.com",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_channel(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.post(
            f"{_PREFIX}/test",
            headers=_admin_headers(admin.id),
            json={
                "scope": "ADMIN",
                "notification_type": "ADMIN_INTERNAL",
                "event": "ADMIN_NEW_ORDER_CREATED",
                "channels": ["PIGEON"],
                "email": "test@example.com",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_email_required_when_email_channel(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.post(
            f"{_PREFIX}/test",
            headers=_admin_headers(admin.id),
            json={
                "scope": "ADMIN",
                "notification_type": "ADMIN_INTERNAL",
                "event": "ADMIN_NEW_ORDER_CREATED",
                "channels": ["EMAIL"],
            },
        )
        assert resp.status_code == 422
