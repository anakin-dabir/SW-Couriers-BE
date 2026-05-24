"""Integration API tests for Service Tiers (v1) — list, create, get, update, delete.

All endpoints require ADMIN role (READ for list/get, WRITE for create/update/delete).
Uses admin user and per-test transaction rollback.

Note: 3 default tiers (Basic, Plus, Professional) are seeded by migration 0017 and are
always present in every test's DB state.
"""

import uuid

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token
from app.modules.user.models import User

SERVICE_TIERS = "/v1/service-tiers"

SEEDED_TIER_COUNT = 4  # Basic, Plus, Professional, Superfast seeded by migrations


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


def _valid_create_payload(
    tier_name: str = "Test Tier",
    duration_days: int = 30,
    price_per_package: float = 9.99,
    available_for: str = "BOTH",
    color: str = "#FFAA00",
    icon: str = "box",
    description: str | None = None,
    status: str = "ACTIVE",
) -> dict:
    payload: dict = {
        "tier_name": tier_name,
        "duration_days": duration_days,
        "price_per_package": price_per_package,
        "available_for": available_for,
        "status": status,
    }
    if color is not None:
        payload["color"] = color
    if icon is not None:
        payload["icon"] = icon
    if description is not None:
        payload["description"] = description
    return payload


class TestListServiceTiers:
    """GET /v1/service-tiers/ — list service tiers."""

    @pytest.mark.asyncio
    async def test_admin_lists_seeded_tiers(self, client: AsyncClient, user_factory) -> None:
        """Migration 0017 seeds default tiers; the list should be non-empty and well-formed."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(SERVICE_TIERS + "/", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] >= 1
        assert len(data["items"]) == data["total"]
        # Every item must have the required fields
        for item in data["items"]:
            assert "id" in item
            assert "tier_name" in item
            assert "duration_days" in item
            assert "price_per_package" in item
            assert "price_per_kg" in item
            assert "base_price" in item
            assert "error_margin_kg" in item
            assert "scope_type" in item
            assert "available_for" in item
            assert "status" in item

    @pytest.mark.asyncio
    async def test_list_includes_new_tier(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        # Get baseline count before creating
        base_resp = await client.get(SERVICE_TIERS + "/", headers=_admin_headers(admin.id))
        base_count = base_resp.json()["data"]["total"]
        await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id), json=_valid_create_payload(tier_name="Extra"))
        resp = await client.get(SERVICE_TIERS + "/", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == base_count + 1

    @pytest.mark.asyncio
    async def test_filter_by_available_for(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(tier_name="B2B Only", available_for="CUSTOMER_B2B"),
        )
        resp = await client.get(SERVICE_TIERS + "/?available_for=CUSTOMER_B2B", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        # Seeded "Basic" is CUSTOMER_B2B + our new one
        assert data["total"] >= 2
        assert all(t["available_for"] == "CUSTOMER_B2B" for t in data["items"])

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(SERVICE_TIERS + "/", headers={"X-Client-Type": "ADMIN"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_customer_without_settings_returns_403(self, client: AsyncClient, verified_user: User) -> None:
        resp = await client.get(SERVICE_TIERS + "/", headers=_customer_headers(verified_user.id))
        assert resp.status_code == 403


class TestCreateServiceTier:
    """POST /v1/service-tiers/ — create service tier."""

    @pytest.mark.asyncio
    async def test_admin_creates_service_tier_with_all_fields(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload(
            tier_name="Gold",
            duration_days=45,
            price_per_package=19.99,
            available_for="BOTH",
            color="#FFAA00",
            icon="box",
            description="Gold tier for premium customers",
            status="ACTIVE",
        )
        resp = await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["tier_name"] == "Gold"
        assert data["duration_days"] == 45
        assert data["price_per_package"] == 19.99
        assert data["available_for"] == "BOTH"
        assert data["color"] == "#FFAA00"
        assert data["icon"] == "box"
        assert data["description"] == "Gold tier for premium customers"
        assert data["status"] == "ACTIVE"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_admin_creates_tier_without_optional_fields(self, client: AsyncClient, user_factory) -> None:
        """color, icon, and description are optional — omitting them should succeed."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = {
            "tier_name": "Minimal Tier",
            "duration_days": 14,
            "price_per_package": 5.00,
            "available_for": "CUSTOMER_B2C",
            "status": "ACTIVE",
        }
        resp = await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["tier_name"] == "Minimal Tier"
        assert data["color"] is None
        assert data["icon"] is None
        assert data["description"] is None

    @pytest.mark.asyncio
    async def test_admin_creates_inactive_tier(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload(tier_name="Hidden Tier", status="INACTIVE")
        resp = await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 201
        assert resp.json()["data"]["status"] == "INACTIVE"

    @pytest.mark.asyncio
    async def test_create_missing_required_fields_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json={"tier_name": "Partial"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_invalid_color_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload()
        payload["color"] = "not-a-color"
        resp = await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_invalid_available_for_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload()
        payload["available_for"] = "INVALID"
        resp = await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_invalid_status_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload()
        payload["status"] = "UNKNOWN"
        resp = await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_unauthenticated_create_returns_401(self, client: AsyncClient) -> None:
        resp = await client.post(
            SERVICE_TIERS + "/",
            headers={"X-Client-Type": "ADMIN"},
            json=_valid_create_payload(),
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_customer_create_returns_403(self, client: AsyncClient, verified_user: User) -> None:
        resp = await client.post(
            SERVICE_TIERS + "/",
            headers=_customer_headers(verified_user.id),
            json=_valid_create_payload(),
        )
        assert resp.status_code == 403


class TestGetServiceTier:
    """GET /v1/service-tiers/{tier_id} — get single service tier."""

    @pytest.mark.asyncio
    async def test_admin_gets_service_tier(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(tier_name="Standard", description="Standard plan"),
        )
        assert create_resp.status_code == 201
        tier_id = create_resp.json()["data"]["id"]

        resp = await client.get(SERVICE_TIERS + f"/{tier_id}", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == tier_id
        assert data["tier_name"] == "Standard"
        assert data["description"] == "Standard plan"
        assert data["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_404(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(
            SERVICE_TIERS + "/00000000-0000-0000-0000-000000000000",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_customer_get_returns_403(self, client: AsyncClient, user_factory, verified_user: User) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(),
        )
        tier_id = create_resp.json()["data"]["id"]
        resp = await client.get(SERVICE_TIERS + f"/{tier_id}", headers=_customer_headers(verified_user.id))
        assert resp.status_code == 403


class TestUpdateServiceTier:
    """PATCH /v1/service-tiers/{tier_id} — update service tier."""

    @pytest.mark.asyncio
    async def test_admin_updates_tier_name_and_audience(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(tier_name="Original"),
        )
        assert create_resp.status_code == 201
        tier_id = create_resp.json()["data"]["id"]

        resp = await client.patch(
            SERVICE_TIERS + f"/{tier_id}",
            headers=_admin_headers(admin.id),
            json={"tier_name": "Updated", "available_for": "CUSTOMER_B2B"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["tier_name"] == "Updated"
        assert data["available_for"] == "CUSTOMER_B2B"

    @pytest.mark.asyncio
    async def test_admin_updates_description(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(tier_name="Tier D"),
        )
        tier_id = create_resp.json()["data"]["id"]

        resp = await client.patch(
            SERVICE_TIERS + f"/{tier_id}",
            headers=_admin_headers(admin.id),
            json={"description": "Now has a description"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["description"] == "Now has a description"

    @pytest.mark.asyncio
    async def test_admin_deactivates_tier(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(tier_name="To Deactivate"),
        )
        tier_id = create_resp.json()["data"]["id"]

        resp = await client.patch(
            SERVICE_TIERS + f"/{tier_id}",
            headers=_admin_headers(admin.id),
            json={"status": "INACTIVE"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "INACTIVE"

        # Re-activate
        resp2 = await client.patch(
            SERVICE_TIERS + f"/{tier_id}",
            headers=_admin_headers(admin.id),
            json={"status": "ACTIVE"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["data"]["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_404(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.patch(
            SERVICE_TIERS + "/00000000-0000-0000-0000-000000000000",
            headers=_admin_headers(admin.id),
            json={"tier_name": "Any"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_customer_update_returns_403(self, client: AsyncClient, user_factory, verified_user: User) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(),
        )
        tier_id = create_resp.json()["data"]["id"]
        resp = await client.patch(
            SERVICE_TIERS + f"/{tier_id}",
            headers=_customer_headers(verified_user.id),
            json={"tier_name": "Hacked"},
        )
        assert resp.status_code == 403


class TestDeleteServiceTier:
    """DELETE /v1/service-tiers/{tier_id} — delete service tier (SETTINGS WRITE)."""

    @pytest.mark.asyncio
    async def test_admin_deletes_service_tier(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(tier_name="To Delete"),
        )
        assert create_resp.status_code == 201
        tier_id = create_resp.json()["data"]["id"]

        resp = await client.delete(SERVICE_TIERS + f"/{tier_id}", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        assert resp.json()["data"] == {}

        get_resp = await client.get(SERVICE_TIERS + f"/{tier_id}", headers=_admin_headers(admin.id))
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.delete(
            SERVICE_TIERS + "/00000000-0000-0000-0000-000000000000",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_customer_delete_returns_403(self, client: AsyncClient, user_factory, verified_user: User) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(),
        )
        tier_id = create_resp.json()["data"]["id"]
        resp = await client.delete(SERVICE_TIERS + f"/{tier_id}", headers=_customer_headers(verified_user.id))
        assert resp.status_code == 403


class TestServiceTierResponseFields:
    """Pricing breakdown, scope, and filters on list responses."""

    @pytest.mark.asyncio
    async def test_response_includes_pricing_breakdown_and_global_scope(
        self, client: AsyncClient, user_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json={
                **_valid_create_payload(tier_name="Breakdown Tier", price_per_package=12.50),
                "base_price": 3.25,
                "price_per_kg": 2.0,
                "error_margin_kg": 5,
            },
        )
        assert create_resp.status_code == 201
        tier_id = create_resp.json()["data"]["id"]

        resp = await client.get(SERVICE_TIERS + f"/{tier_id}", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["scope_type"] == "GLOBAL"
        assert data["scope_org_id"] is None
        assert data["base_price"] == 3.25
        assert data["price_per_kg"] == 2.0
        assert data["price_per_package"] == 12.5
        assert data["error_margin_kg"] == 5

    @pytest.mark.asyncio
    async def test_list_response_items_carry_scope_and_breakdown_fields(
        self, client: AsyncClient, user_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(SERVICE_TIERS + "/", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        for item in resp.json()["data"]["items"]:
            assert "base_price" in item, f"base_price missing from tier {item.get('id')}"
            assert "price_per_kg" in item
            assert "error_margin_kg" in item
            assert "scope_type" in item
            assert item["scope_type"] in ("GLOBAL", "ORG")

    @pytest.mark.asyncio
    async def test_r2_key_not_exposed_in_tier_response(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Internal R2 key must never appear in the service tier response."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(tier_name="No Leak Tier"),
        )
        assert create_resp.status_code == 201
        assert "r2_key" not in create_resp.json()["data"]

    @pytest.mark.asyncio
    async def test_multi_filter_status(self, client: AsyncClient, user_factory) -> None:
        """Passing status=ACTIVE&status=INACTIVE should return tiers of both statuses."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id),
                          json=_valid_create_payload(tier_name="Active Tier", status="ACTIVE"))
        await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id),
                          json=_valid_create_payload(tier_name="Inactive Tier", status="INACTIVE"))

        resp = await client.get(
            SERVICE_TIERS + "/?status=ACTIVE&status=INACTIVE",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 200
        statuses = {t["status"] for t in resp.json()["data"]["items"]}
        assert "ACTIVE" in statuses
        assert "INACTIVE" in statuses

    @pytest.mark.asyncio
    async def test_filter_by_price_range(self, client: AsyncClient, user_factory) -> None:
        """min_price / max_price filters return only tiers within range."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id),
                          json=_valid_create_payload(tier_name="Cheap", price_per_package=5.00))
        await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id),
                          json=_valid_create_payload(tier_name="Expensive", price_per_package=100.00))

        resp = await client.get(
            SERVICE_TIERS + "/?min_price=4&max_price=10",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 200
        prices = [t["price_per_package"] for t in resp.json()["data"]["items"]]
        assert all(4 <= p <= 10 for p in prices)

    @pytest.mark.asyncio
    async def test_filter_by_days_range(self, client: AsyncClient, user_factory) -> None:
        """min_days / max_days filters return only tiers within duration range."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id),
                          json=_valid_create_payload(tier_name="Short", duration_days=7))
        await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id),
                          json=_valid_create_payload(tier_name="Long", duration_days=365))

        resp = await client.get(
            SERVICE_TIERS + "/?min_days=1&max_days=30",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 200
        days = [t["duration_days"] for t in resp.json()["data"]["items"]]
        assert all(1 <= d <= 30 for d in days)

    @pytest.mark.asyncio
    async def test_search_by_name(self, client: AsyncClient, user_factory) -> None:
        """search= param should match on tier_name."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id),
                          json=_valid_create_payload(tier_name="UniqueSearchableName"))

        resp = await client.get(
            SERVICE_TIERS + "/?search=UniqueSearchableName",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] >= 1
        assert any("UniqueSearchableName" in t["tier_name"] for t in data["items"])


class TestServiceTierEffectiveAndOrgOverrides:
    """GET effective-for-org and PUT org overrides (aligned with suspension rules patterns)."""

    @pytest.mark.asyncio
    async def test_effective_list_merges_org_override_over_global(
        self, client: AsyncClient, user_factory, org_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        org = await org_factory()

        list_resp = await client.get(SERVICE_TIERS + "/", headers=_admin_headers(admin.id))
        assert list_resp.status_code == 200
        global_tier = next(
            t for t in list_resp.json()["data"]["items"] if t["scope_type"] == "GLOBAL" and t["status"] == "ACTIVE"
        )
        name = global_tier["tier_name"]
        aud = global_tier["available_for"]
        orig_pkg = float(global_tier["price_per_package"])

        put = await client.put(
            f"{SERVICE_TIERS}/orgs/{org.id}/overrides",
            headers=_admin_headers(admin.id),
            json={
                "tier_name": name,
                "available_for": aud,
                "price_per_package": round(orig_pkg + 10.01, 2),
                "base_price": 1.0,
            },
        )
        assert put.status_code == 200
        assert put.json()["data"]["scope_type"] == "ORG"
        assert put.json()["data"]["scope_org_id"] == org.id

        eff = await client.get(
            f"{SERVICE_TIERS}/effective-for-org/{org.id}",
            headers=_admin_headers(admin.id),
        )
        assert eff.status_code == 200
        row = next(t for t in eff.json()["data"]["items"] if t["tier_name"] == name and t["available_for"] == aud)
        assert row["is_override"] is True
        assert row["global_tier_id"] == global_tier["id"]
        assert float(row["price_per_package"]) == round(orig_pkg + 10.01, 2)

    @pytest.mark.asyncio
    async def test_list_filter_scope_org_returns_only_org_rows(
        self, client: AsyncClient, user_factory, org_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        org = await org_factory()
        unique = f"OrgOnly{org.id[:8]}"
        create = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json={
                "tier_name": unique,
                "duration_days": 14,
                "price_per_package": 11.0,
                "available_for": "BOTH",
                "scope_type": "ORG",
                "scope_org_id": org.id,
                "status": "ACTIVE",
            },
        )
        assert create.status_code == 201

        resp = await client.get(
            f"{SERVICE_TIERS}/?scope_type=ORG&scope_org_id={org.id}",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert all(t["scope_type"] == "ORG" and t["scope_org_id"] == org.id for t in items)
        assert any(t["tier_name"] == unique for t in items)


class TestServiceTierValidationAndSecurity:
    """422 paths, duplicate constraints, UUID paths, and request hardening."""

    @pytest.mark.asyncio
    async def test_list_scope_org_without_org_id_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(
            f"{SERVICE_TIERS}/?scope_type=ORG",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_list_global_with_scope_org_id_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(
            f"{SERVICE_TIERS}/?scope_type=GLOBAL&scope_org_id=00000000-0000-0000-0000-000000000001",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_list_min_price_gt_max_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(
            f"{SERVICE_TIERS}/?min_price=100&max_price=10",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_duplicate_global_same_name_audience_returns_422(
        self, client: AsyncClient, user_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        name = f"UniqueDupTestName-{uuid.uuid4().hex[:12]}"
        first = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(tier_name=name, available_for="BOTH"),
        )
        assert first.status_code == 201
        second = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(tier_name=name, available_for="BOTH"),
        )
        assert second.status_code == 422

    @pytest.mark.asyncio
    async def test_effective_for_unknown_org_returns_404(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(
            f"{SERVICE_TIERS}/effective-for-org/00000000-0000-0000-0000-000000000099",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_patch_unknown_field_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create = await client.post(
            SERVICE_TIERS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(tier_name="PatchUnknownField"),
        )
        assert create.status_code == 201
        tid = create.json()["data"]["id"]
        resp = await client.patch(
            SERVICE_TIERS + f"/{tid}",
            headers=_admin_headers(admin.id),
            json={"not_allowed": 1},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_price_above_cap_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload(tier_name="TooRich")
        payload["price_per_package"] = 2_000_000.0
        resp = await client.post(SERVICE_TIERS + "/", headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_invalid_uuid_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(f"{SERVICE_TIERS}/not-a-uuid", headers=_admin_headers(admin.id))
        assert resp.status_code == 422
