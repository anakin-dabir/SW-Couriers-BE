"""API and edge-case tests for the system-owned Superfast service tier."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.organizations.models import OrgServiceTierContractLine
from app.modules.service_tiers.constants import SUPERFAST_AVAILABLE_FOR, SUPERFAST_TIER_NAME
from app.modules.service_tiers.models import ServiceTier

SERVICE_TIERS = "/v1/service-tiers"


def _admin_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="ADMIN", client_type="ADMIN")
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "ADMIN"}


def _find_superfast(items: list[dict]) -> dict:
    for item in items:
        if item["tier_name"] == SUPERFAST_TIER_NAME and item["scope_type"] == "GLOBAL":
            return item
    raise AssertionError("Superfast global tier not found")


def _valid_create_payload(**overrides) -> dict:
    payload = {
        "tier_name": f"TempTier-{uuid.uuid4().hex[:8]}",
        "duration_days": 14,
        "price_per_package": 19.99,
        "available_for": "BOTH",
        "color": "#AABBCC",
        "icon": "box",
        "status": "ACTIVE",
    }
    payload.update(overrides)
    return payload


class TestSuperfastGlobalCatalog:
    @pytest.mark.asyncio
    async def test_global_list_includes_superfast(
        self, client: AsyncClient, user_factory, superfast_global_tier: ServiceTier
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(f"{SERVICE_TIERS}/global", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        tier = _find_superfast(resp.json()["data"]["items"])
        assert tier["id"] == superfast_global_tier.id
        assert tier["is_system_tier"] is True
        assert tier["tier_name_locked"] is True
        assert tier["permitted_locked"] is False
        assert tier["price_per_package"] > 0

    @pytest.mark.asyncio
    async def test_main_list_includes_superfast(
        self, client: AsyncClient, user_factory, superfast_global_tier
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(f"{SERVICE_TIERS}/", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        _find_superfast(resp.json()["data"]["items"])

    @pytest.mark.asyncio
    async def test_get_by_id_returns_system_flags(self, client: AsyncClient, user_factory, superfast_global_tier) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(f"{SERVICE_TIERS}/{superfast_global_tier.id}", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["tier_name"] == SUPERFAST_TIER_NAME
        assert data["is_system_tier"] is True
        assert data["tier_name_locked"] is True


class TestSuperfastMutationGuards:
    @pytest.mark.asyncio
    async def test_cannot_delete_superfast(self, client: AsyncClient, user_factory, superfast_global_tier) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.delete(f"{SERVICE_TIERS}/{superfast_global_tier.id}", headers=_admin_headers(admin.id))
        assert resp.status_code == 422
        assert "Superfast" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_cannot_rename_superfast(self, client: AsyncClient, user_factory, superfast_global_tier) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.patch(
            f"{SERVICE_TIERS}/{superfast_global_tier.id}",
            headers=_admin_headers(admin.id),
            json={"tier_name": "Renamed", "version": superfast_global_tier.version},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_cannot_deactivate_superfast(self, client: AsyncClient, user_factory, superfast_global_tier) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.patch(
            f"{SERVICE_TIERS}/{superfast_global_tier.id}",
            headers=_admin_headers(admin.id),
            json={"status": "INACTIVE", "version": superfast_global_tier.version},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_cannot_change_superfast_audience(self, client: AsyncClient, user_factory, superfast_global_tier) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.patch(
            f"{SERVICE_TIERS}/{superfast_global_tier.id}",
            headers=_admin_headers(admin.id),
            json={"available_for": "CUSTOMER_B2B", "version": superfast_global_tier.version},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_cannot_create_superfast_via_post(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.post(
            f"{SERVICE_TIERS}/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(tier_name=SUPERFAST_TIER_NAME),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_can_update_superfast_price_and_metadata(
        self, client: AsyncClient, user_factory, superfast_global_tier
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.patch(
            f"{SERVICE_TIERS}/{superfast_global_tier.id}",
            headers=_admin_headers(admin.id),
            json={
                "price_per_package": 130.0,
                "duration_days": 2,
                "description": "Updated express tier",
                "color": "#112233",
                "icon": "flash",
                "version": superfast_global_tier.version,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["price_per_package"] == 130.0
        assert data["duration_days"] == 2
        assert data["description"] == "Updated express tier"
        assert data["tier_name"] == SUPERFAST_TIER_NAME


class TestSuperfastOrgEffectiveAndOverrides:
    @pytest.mark.asyncio
    async def test_effective_for_org_superfast_is_permitted_locked(
        self, client: AsyncClient, user_factory, org_factory, superfast_global_tier
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        org = await org_factory()
        resp = await client.get(
            f"{SERVICE_TIERS}/effective-for-org/{org.id}",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 200
        row = next(t for t in resp.json()["data"]["items"] if t["tier_name"] == SUPERFAST_TIER_NAME)
        assert row["permitted"] is True
        assert row["permitted_locked"] is True
        assert row["is_system_tier"] is True
        assert row["tier_name_locked"] is True

    @pytest.mark.asyncio
    async def test_org_override_upsert_custom_superfast_price(
        self, client: AsyncClient, user_factory, org_factory, superfast_global_tier
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        org = await org_factory()
        resp = await client.put(
            f"{SERVICE_TIERS}/orgs/{org.id}/overrides",
            headers=_admin_headers(admin.id),
            json={
                "tier_name": SUPERFAST_TIER_NAME,
                "available_for": SUPERFAST_AVAILABLE_FOR,
                "price_per_package": 199.99,
                "duration_days": 1,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["scope_type"] == "ORG"
        assert data["tier_name"] == SUPERFAST_TIER_NAME
        assert float(data["price_per_package"]) == 199.99

        eff = await client.get(
            f"{SERVICE_TIERS}/effective-for-org/{org.id}",
            headers=_admin_headers(admin.id),
        )
        row = next(t for t in eff.json()["data"]["items"] if t["tier_name"] == SUPERFAST_TIER_NAME)
        assert row["is_override"] is True
        assert float(row["price_per_package"]) == 199.99
        assert row["permitted"] is True


class TestSuperfastRegression:
    @pytest.mark.asyncio
    async def test_other_global_tier_still_deletable(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create = await client.post(
            f"{SERVICE_TIERS}/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(),
        )
        assert create.status_code == 201
        tier_id = create.json()["data"]["id"]
        delete = await client.delete(f"{SERVICE_TIERS}/{tier_id}", headers=_admin_headers(admin.id))
        assert delete.status_code == 200

    @pytest.mark.asyncio
    async def test_other_global_tier_still_renamable(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create = await client.post(
            f"{SERVICE_TIERS}/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(),
        )
        assert create.status_code == 201
        tier = create.json()["data"]
        patch = await client.patch(
            f"{SERVICE_TIERS}/{tier['id']}",
            headers=_admin_headers(admin.id),
            json={"tier_name": "RenamedTemp", "version": tier["version"]},
        )
        assert patch.status_code == 200
        assert patch.json()["data"]["tier_name"] == "RenamedTemp"


class TestSuperfastContractSyncEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_pricing_plans_sync_creates_superfast_contract(
        self, db_session: AsyncSession, org_factory, superfast_global_tier
    ) -> None:
        from app.modules.organizations.pricing_plans_contract_sync import replace_org_contract_from_pricing_plans

        org = await org_factory()
        await replace_org_contract_from_pricing_plans(db_session, organization_id=org.id, plans=[])
        await db_session.flush()

        lines = (
            await db_session.execute(
                select(OrgServiceTierContractLine).where(OrgServiceTierContractLine.organization_id == org.id)
            )
        ).scalars().all()
        assert len(lines) == 1
        assert lines[0].global_template_id == superfast_global_tier.id
        assert lines[0].permitted is True

    @pytest.mark.asyncio
    async def test_superfast_still_exists_after_other_tier_delete(
        self, client: AsyncClient, user_factory, superfast_global_tier
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create = await client.post(
            f"{SERVICE_TIERS}/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(),
        )
        temp_id = create.json()["data"]["id"]
        await client.delete(f"{SERVICE_TIERS}/{temp_id}", headers=_admin_headers(admin.id))

        listed = await client.get(f"{SERVICE_TIERS}/global", headers=_admin_headers(admin.id))
        assert listed.status_code == 200
        _find_superfast(listed.json()["data"]["items"])
