"""API-level tests for permission endpoints.

Validates HTTP status codes, response shapes, and auth guards
by hitting the actual ASGI app via httpx.
"""

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token

WEB_ADMIN_HEADERS = {"X-Client-Type": "ADMIN"}


def _admin_headers(user_id: str, role: str = "ADMIN") -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role=role, client_type="ADMIN")
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "ADMIN",
    }


def _customer_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="CUSTOMER_B2C", client_type="CUSTOMER_B2C")
    return {
        "Authorization": f"Bearer {token}",
        "X-Client-Type": "CUSTOMER_B2C",
    }



class TestGetMyPermissions:
    """GET /permissions/me — any authenticated user."""

    @pytest.mark.asyncio
    async def test_returns_own_permissions(self, client: AsyncClient, verified_user) -> None:
        headers = _customer_headers(verified_user.id)
        resp = await client.get("/v1/permissions/me", headers=headers)
        assert resp.status_code == 200

        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["user_id"] == verified_user.id
        assert data["role"] == verified_user.role
        assert isinstance(data["permissions"], list)
        assert len(data["permissions"]) > 0

        resources_in_response = {p["resource"] for p in data["permissions"]}
        assert "DASHBOARD" in resources_in_response
        assert "SHIPMENTS" in resources_in_response

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get("/v1/permissions/me", headers={"X-Client-Type": "ADMIN"})
        assert resp.status_code == 401


class TestGetAvailableResources:
    """GET /permissions/resources — admin only."""

    @pytest.mark.asyncio
    async def test_admin_gets_resources_and_levels(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.get("/v1/permissions/resources", headers=headers)
        assert resp.status_code == 200

        data = resp.json()["data"]
        assert "DASHBOARD" in data["resources"]
        assert "SHIPMENTS" in data["resources"]
        assert "NONE" in data["levels"]
        assert "READ" in data["levels"]
        assert "WRITE" in data["levels"]

    @pytest.mark.asyncio
    async def test_non_admin_gets_403(self, client: AsyncClient, verified_user) -> None:
        headers = _customer_headers(verified_user.id)
        resp = await client.get("/v1/permissions/resources", headers=headers)
        assert resp.status_code == 403


class TestSetPermission:
    """PUT /permissions/{user_id} — admin only."""

    @pytest.mark.asyncio
    async def test_admin_can_set_permission(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.put(
            f"/v1/permissions/{target.id}",
            headers=headers,
            json={"resource": "DASHBOARD", "level": "READ"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["user_id"] == target.id

    @pytest.mark.asyncio
    async def test_invalid_resource_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.put(
            f"/v1/permissions/{target.id}",
            headers=headers,
            json={"resource": "NONEXISTENT", "level": "READ"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_level_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.put(
            f"/v1/permissions/{target.id}",
            headers=headers,
            json={"resource": "DASHBOARD", "level": "SUPERADMIN"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_nonexistent_user_returns_404(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.put(
            "/v1/permissions/00000000-0000-0000-0000-000000000000",
            headers=headers,
            json={"resource": "DASHBOARD", "level": "READ"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_non_admin_cannot_set_permission(self, client: AsyncClient, verified_user, user_factory) -> None:
        target = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        headers = _customer_headers(verified_user.id)

        resp = await client.put(
            f"/v1/permissions/{target.id}",
            headers=headers,
            json={"resource": "DASHBOARD", "level": "READ"},
        )
        assert resp.status_code == 403



class TestGetUserPermissions:
    """GET /permissions/{user_id} — admin views another user's permissions."""

    @pytest.mark.asyncio
    async def test_admin_can_view_user_permissions(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.get(f"/v1/permissions/{target.id}", headers=headers)
        assert resp.status_code == 200

        data = resp.json()["data"]
        assert data["user_id"] == target.id
        assert data["role"] == "DRIVER"
        assert isinstance(data["permissions"], list)
        assert len(data["permissions"]) > 0

        sources = {p["source"] for p in data["permissions"]}
        assert "role_default" in sources

    @pytest.mark.asyncio
    async def test_admin_sees_overrides_after_set(self, client: AsyncClient, user_factory) -> None:
        """After setting an override, GET should reflect the change."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        await client.put(
            f"/v1/permissions/{target.id}",
            headers=headers,
            json={"resource": "DASHBOARD", "level": "WRITE"},
        )

        resp = await client.get(f"/v1/permissions/{target.id}", headers=headers)
        assert resp.status_code == 200

        perms = {p["resource"]: p for p in resp.json()["data"]["permissions"]}
        assert perms["DASHBOARD"]["level"] == "WRITE"
        assert perms["DASHBOARD"]["source"] == "override"

    @pytest.mark.asyncio
    async def test_nonexistent_user_returns_404(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.get(
            "/v1/permissions/00000000-0000-0000-0000-000000000000",
            headers=headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_non_admin_cannot_view_other_user(self, client: AsyncClient, verified_user, user_factory) -> None:
        target = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        headers = _customer_headers(verified_user.id)

        resp = await client.get(f"/v1/permissions/{target.id}", headers=headers)
        assert resp.status_code == 403


class TestBulkSetPermissions:
    """PUT /permissions/{user_id}/bulk — admin only."""

    @pytest.mark.asyncio
    async def test_admin_can_bulk_set(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.put(
            f"/v1/permissions/{target.id}/bulk",
            headers=headers,
            json={
                "permissions": [
                    {"resource": "DASHBOARD", "level": "READ"},
                    {"resource": "INVOICES", "level": "WRITE"},
                ]
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["user_id"] == target.id

        get_resp = await client.get(f"/v1/permissions/{target.id}", headers=headers)
        perms = {p["resource"]: p for p in get_resp.json()["data"]["permissions"]}
        assert perms["DASHBOARD"]["level"] == "READ"
        assert perms["DASHBOARD"]["source"] == "override"
        assert perms["INVOICES"]["level"] == "WRITE"
        assert perms["INVOICES"]["source"] == "override"

    @pytest.mark.asyncio
    async def test_bulk_set_replaces_previous_overrides(self, client: AsyncClient, user_factory) -> None:
        """A second bulk set should clear the first one's overrides."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        await client.put(
            f"/v1/permissions/{target.id}/bulk",
            headers=headers,
            json={"permissions": [{"resource": "DASHBOARD", "level": "WRITE"}]},
        )

        await client.put(
            f"/v1/permissions/{target.id}/bulk",
            headers=headers,
            json={"permissions": [{"resource": "INVOICES", "level": "READ"}]},
        )

        get_resp = await client.get(f"/v1/permissions/{target.id}", headers=headers)
        perms = {p["resource"]: p for p in get_resp.json()["data"]["permissions"]}
        assert perms["DASHBOARD"]["source"] == "role_default"
        assert perms["INVOICES"]["level"] == "READ"
        assert perms["INVOICES"]["source"] == "override"

    @pytest.mark.asyncio
    async def test_bulk_set_nonexistent_user_returns_404(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.put(
            "/v1/permissions/00000000-0000-0000-0000-000000000000/bulk",
            headers=headers,
            json={"permissions": [{"resource": "DASHBOARD", "level": "READ"}]},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_non_admin_cannot_bulk_set(self, client: AsyncClient, verified_user, user_factory) -> None:
        target = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        headers = _customer_headers(verified_user.id)

        resp = await client.put(
            f"/v1/permissions/{target.id}/bulk",
            headers=headers,
            json={"permissions": [{"resource": "DASHBOARD", "level": "READ"}]},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_empty_permissions_list_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.put(
            f"/v1/permissions/{target.id}/bulk",
            headers=headers,
            json={"permissions": []},
        )
        assert resp.status_code == 422



class TestResetPermissions:
    """DELETE /permissions/{user_id} — admin only."""

    @pytest.mark.asyncio
    async def test_admin_can_reset_permissions(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        await client.put(
            f"/v1/permissions/{target.id}",
            headers=headers,
            json={"resource": "DASHBOARD", "level": "WRITE"},
        )

        resp = await client.delete(f"/v1/permissions/{target.id}", headers=headers)
        assert resp.status_code == 200
        assert "reset" in resp.json()["data"]["message"].lower()

    @pytest.mark.asyncio
    async def test_reset_reverts_overrides_to_defaults(self, client: AsyncClient, user_factory) -> None:
        """After reset, GET should show all permissions as role_default."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        await client.put(
            f"/v1/permissions/{target.id}",
            headers=headers,
            json={"resource": "DASHBOARD", "level": "WRITE"},
        )

        await client.delete(f"/v1/permissions/{target.id}", headers=headers)

        get_resp = await client.get(f"/v1/permissions/{target.id}", headers=headers)
        sources = {p["source"] for p in get_resp.json()["data"]["permissions"]}
        assert sources == {"role_default"}

    @pytest.mark.asyncio
    async def test_reset_nonexistent_user_returns_404(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)

        resp = await client.delete(
            "/v1/permissions/00000000-0000-0000-0000-000000000000",
            headers=headers,
        )
        assert resp.status_code == 404
