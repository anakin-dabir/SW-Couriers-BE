"""Integration API tests — client inactivity configuration."""

import pytest
from httpx import AsyncClient

CONFIG = "/v1/client-inactivity-config"


class TestClientInactivityConfigApi:
    @pytest.mark.asyncio
    async def test_get_seeds_defaults(self, client: AsyncClient, admin_headers: dict) -> None:
        resp = await client.get(CONFIG, headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["enabled"] is True
        assert data["inactive_after_days"] == 60
        assert "version" in data

    @pytest.mark.asyncio
    async def test_patch_updates_threshold(self, client: AsyncClient, admin_headers: dict) -> None:
        seeded = await client.get(CONFIG, headers=admin_headers)
        version = seeded.json()["data"]["version"]
        resp = await client.patch(
            CONFIG,
            headers=admin_headers,
            json={"inactive_after_days": 45, "version": version},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["inactive_after_days"] == 45

    @pytest.mark.asyncio
    async def test_patch_disable_policy(self, client: AsyncClient, admin_headers: dict) -> None:
        seeded = await client.get(CONFIG, headers=admin_headers)
        version = seeded.json()["data"]["version"]
        resp = await client.patch(
            CONFIG,
            headers=admin_headers,
            json={"enabled": False, "version": version},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_patch_rejects_invalid_threshold(self, client: AsyncClient, admin_headers: dict) -> None:
        seeded = await client.get(CONFIG, headers=admin_headers)
        version = seeded.json()["data"]["version"]
        resp = await client.patch(
            CONFIG,
            headers=admin_headers,
            json={"inactive_after_days": 0, "version": version},
        )
        assert resp.status_code == 422
