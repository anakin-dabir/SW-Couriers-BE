"""Integration API tests — OrgDiscountConfig endpoints.

Schema (migration 0072):
- One row per (organization_id, service_tier_id, discount_type)
- UNIQUE on (organization_id, service_tier_id, discount_type)
- discount_type: PERCENTAGE | FIXED_PER_BOOKING | VOLUME_TIERED
- PERCENTAGE / FIXED_PER_BOOKING: value, valid_from (required), valid_until (optional)
- VOLUME_TIERED: volume_tiers JSONB [{min_bookings, max_bookings, discount_pct}]
- is_enabled: admin can disable without deleting

Covers:
- POST   /v1/organizations              create org with inline discount_config
- GET    /v1/organizations/{id}/discount-config
- PUT    /v1/organizations/{id}/discount-config   (upsert — replaces all rows)
- DELETE /v1/organizations/{id}/discount-config

All tests use per-test transaction rollback (no persistent state).
Arq background jobs are mocked so no Redis/worker is needed.
"""

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

ORGS = "/v1/organizations"


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _org_form_data(email: str | None = None) -> dict:
    contacts = [
        {
            "email": email or f"owner-{uuid.uuid4().hex[:8]}@disctest.com",
            "first_name": "Disc",
            "last_name": "Owner",
            "contact_number": "+447700900002",
            "contact_role": "ACCOUNT_OWNER",
        }
    ]
    return {
        "trading_name": "Discount Test Ltd",
        "legal_entity_name": "Discount Test Limited",
        "companies_house_number": f"DT{uuid.uuid4().hex[:6].upper()}",
        "vat_number": f"GB{uuid.uuid4().int % 10**9:09d}",
        "date_of_incorporation": "2019-03-15",
        "industry": "LOGISTICS_TRANSPORT",
        "company_size": "11-50 employees",
        "reg_address_line_1": "99 Discount Lane",
        "reg_city": "Manchester",
        "reg_postcode": "M1 1AA",
        "contacts": json.dumps(contacts),
    }


def _percentage_item(tier_id: str, value: str = "10.00", enabled: bool = True) -> dict:
    return {
        "service_tier_id": tier_id,
        "discount_type": "PERCENTAGE",
        "is_enabled": enabled,
        "value": value,
        "valid_from": "2026-01-01",
        "valid_until": "2026-12-31",
    }


def _fixed_item(tier_id: str, value: str = "5.00", enabled: bool = True) -> dict:
    return {
        "service_tier_id": tier_id,
        "discount_type": "FIXED_PER_BOOKING",
        "is_enabled": enabled,
        "value": value,
        "valid_from": "2026-01-01",
        "valid_until": None,
    }


def _volume_item(tier_id: str, enabled: bool = True) -> dict:
    return {
        "service_tier_id": tier_id,
        "discount_type": "VOLUME_TIERED",
        "is_enabled": enabled,
        "volume_tiers": [
            {"min_bookings": 1, "max_bookings": 50, "discount_pct": "5.00"},
            {"min_bookings": 51, "max_bookings": 200, "discount_pct": "10.00"},
            {"min_bookings": 201, "max_bookings": None, "discount_pct": "15.00"},
        ],
    }


def _discount_config_input(discounts: list[dict]) -> dict:
    return {"discounts": discounts}


def _discount_config_upsert(discounts: list[dict], reason: str = "Setting up discount tiers") -> dict:
    return {"discounts": discounts, "reason": reason}


def _mock_enqueue():
    return patch("app.modules.organizations.service.enqueue", new_callable=AsyncMock, return_value=None)


def _mock_create_invite():
    from app.modules.auth.service import CreateInviteResult

    fake_invite = MagicMock()
    fake_invite.id = "invite-id-fake"
    fake_user = MagicMock()
    return patch(
        "app.modules.organizations.service.AuthService.create_invite",
        new_callable=AsyncMock,
        return_value=CreateInviteResult(False, fake_invite, "raw-token-abc123", fake_user, "invite-id-fake"),
    )


async def _create_org(
    client: AsyncClient,
    admin_headers: dict,
    discount_config: dict | None = None,
    email: str | None = None,
) -> tuple[str, dict | None]:
    """Create an org, optionally with inline discount_config. Returns (org_id, discount_config_data)."""
    data = _org_form_data(email)
    if discount_config is not None:
        data["discount_config"] = json.dumps(discount_config)
    with _mock_enqueue(), _mock_create_invite():
        resp = await client.post(ORGS, data=data, headers=admin_headers)
    assert resp.status_code == 201, resp.text
    resp_data = resp.json()["data"]
    return resp_data["organization"]["id"], resp_data.get("discount_config")


# ═══════════════════════════════════════════════════════════════════════════════
#  CREATE (inline during org creation)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateOrgWithDiscountConfig:
    """POST /v1/organizations — discount_config is optional and created atomically."""

    @pytest.mark.asyncio
    async def test_create_without_discount_config_returns_null(
        self, client: AsyncClient, admin_headers: dict
    ) -> None:
        """When discount_config is omitted the field is null in the response."""
        _, dc = await _create_org(client, admin_headers)
        assert dc is None

    @pytest.mark.asyncio
    async def test_create_with_percentage_discount(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Single PERCENTAGE discount item is stored and returned correctly."""
        config = _discount_config_input([_percentage_item(pricing_tier_ids[0], "15.00")])
        org_id, dc = await _create_org(client, admin_headers, config)

        assert dc is not None
        assert dc["organization_id"] == org_id
        assert len(dc["discounts"]) == 1
        item = dc["discounts"][0]
        assert item["discount_type"] == "PERCENTAGE"
        assert item["service_tier_id"] == pricing_tier_ids[0]
        assert Decimal(item["value"]) == Decimal("15.00")
        assert item["valid_from"] == "2026-01-01"
        assert item["valid_until"] == "2026-12-31"
        assert item["is_enabled"] is True

    @pytest.mark.asyncio
    async def test_create_with_fixed_discount(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """FIXED_PER_BOOKING discount item is stored correctly."""
        config = _discount_config_input([_fixed_item(pricing_tier_ids[0], "25.00")])
        _, dc = await _create_org(client, admin_headers, config)

        assert dc is not None
        item = dc["discounts"][0]
        assert item["discount_type"] == "FIXED_PER_BOOKING"
        assert Decimal(item["value"]) == Decimal("25.00")
        assert item["valid_until"] is None

    @pytest.mark.asyncio
    async def test_create_with_volume_tiered_discount(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """VOLUME_TIERED discount item is stored with all tier brackets."""
        config = _discount_config_input([_volume_item(pricing_tier_ids[0])])
        _, dc = await _create_org(client, admin_headers, config)

        assert dc is not None
        item = dc["discounts"][0]
        assert item["discount_type"] == "VOLUME_TIERED"
        assert len(item["volume_tiers"]) == 3
        assert item["volume_tiers"][0]["min_bookings"] == 1
        assert item["volume_tiers"][0]["max_bookings"] == 50
        assert item["volume_tiers"][2]["max_bookings"] is None  # open-ended last tier

    @pytest.mark.asyncio
    async def test_create_multiple_discounts_across_tiers(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Multiple (tier, type) combinations are all stored."""
        config = _discount_config_input([
            _percentage_item(pricing_tier_ids[0]),
            _percentage_item(pricing_tier_ids[1], "5.00"),
            _fixed_item(pricing_tier_ids[0]),
        ])
        _, dc = await _create_org(client, admin_headers, config)

        assert dc is not None
        assert len(dc["discounts"]) == 3

    @pytest.mark.asyncio
    async def test_create_disabled_discount(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """is_enabled=False is persisted (discount configured but inactive)."""
        config = _discount_config_input([_percentage_item(pricing_tier_ids[0], enabled=False)])
        _, dc = await _create_org(client, admin_headers, config)

        assert dc is not None
        assert dc["discounts"][0]["is_enabled"] is False

    # ── Validation errors ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_percentage_missing_value_returns_422(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """PERCENTAGE without value → 422."""
        bad = {
            "service_tier_id": pricing_tier_ids[0],
            "discount_type": "PERCENTAGE",
            "valid_from": "2026-01-01",
        }
        data = _org_form_data()
        data["discount_config"] = json.dumps(_discount_config_input([bad]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_percentage_missing_valid_from_returns_422(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """PERCENTAGE without valid_from → 422."""
        bad = {
            "service_tier_id": pricing_tier_ids[0],
            "discount_type": "PERCENTAGE",
            "value": "10.00",
        }
        data = _org_form_data()
        data["discount_config"] = json.dumps(_discount_config_input([bad]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_percentage_value_above_100_returns_422(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """PERCENTAGE with value > 100 → 422."""
        bad = _percentage_item(pricing_tier_ids[0], value="101.00")
        data = _org_form_data()
        data["discount_config"] = json.dumps(_discount_config_input([bad]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_valid_until_before_valid_from_returns_422(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """valid_until <= valid_from → 422."""
        bad = {
            **_percentage_item(pricing_tier_ids[0]),
            "valid_from": "2026-06-01",
            "valid_until": "2026-01-01",
        }
        data = _org_form_data()
        data["discount_config"] = json.dumps(_discount_config_input([bad]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_volume_tiered_missing_tiers_returns_422(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """VOLUME_TIERED without volume_tiers → 422."""
        bad = {
            "service_tier_id": pricing_tier_ids[0],
            "discount_type": "VOLUME_TIERED",
        }
        data = _org_form_data()
        data["discount_config"] = json.dumps(_discount_config_input([bad]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_volume_tiers_not_starting_at_1_returns_422(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Volume tiers not starting at min_bookings=1 → 422."""
        bad = {
            "service_tier_id": pricing_tier_ids[0],
            "discount_type": "VOLUME_TIERED",
            "volume_tiers": [
                {"min_bookings": 5, "max_bookings": None, "discount_pct": "10.00"},
            ],
        }
        data = _org_form_data()
        data["discount_config"] = json.dumps(_discount_config_input([bad]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_volume_tiers_with_gap_returns_422(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Volume tiers with a gap between brackets → 422."""
        bad = {
            "service_tier_id": pricing_tier_ids[0],
            "discount_type": "VOLUME_TIERED",
            "volume_tiers": [
                {"min_bookings": 1, "max_bookings": 50, "discount_pct": "5.00"},
                # gap: 51-99 missing
                {"min_bookings": 100, "max_bookings": None, "discount_pct": "15.00"},
            ],
        }
        data = _org_form_data()
        data["discount_config"] = json.dumps(_discount_config_input([bad]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_volume_tiers_last_not_open_ended_returns_422(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Last volume tier must have max_bookings=null → 422 otherwise."""
        bad = {
            "service_tier_id": pricing_tier_ids[0],
            "discount_type": "VOLUME_TIERED",
            "volume_tiers": [
                {"min_bookings": 1, "max_bookings": 100, "discount_pct": "10.00"},
                # last tier has max_bookings set — invalid
            ],
        }
        data = _org_form_data()
        data["discount_config"] = json.dumps(_discount_config_input([bad]))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_duplicate_tier_type_combination_returns_422(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Two items with the same (service_tier_id, discount_type) → 422."""
        config = _discount_config_input([
            _percentage_item(pricing_tier_ids[0]),
            _percentage_item(pricing_tier_ids[0]),  # duplicate
        ])
        data = _org_form_data()
        data["discount_config"] = json.dumps(config)
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
#  GET
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetDiscountConfig:
    """GET /v1/organizations/{id}/discount-config"""

    @pytest.mark.asyncio
    async def test_admin_can_get_discount_config(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Admin retrieves discount config with all discount rows."""
        config = _discount_config_input([
            _percentage_item(pricing_tier_ids[0]),
            _fixed_item(pricing_tier_ids[1]),
        ])
        org_id, _ = await _create_org(client, admin_headers, config)

        resp = await client.get(f"{ORGS}/{org_id}/discount-config", headers=admin_headers)

        assert resp.status_code == 200
        dc = resp.json()["data"]
        assert dc["organization_id"] == org_id
        assert len(dc["discounts"]) == 2

    @pytest.mark.asyncio
    async def test_get_returns_null_when_no_config(
        self, client: AsyncClient, admin_headers: dict, org_factory
    ) -> None:
        """Org with no discount config → data is null (not 404)."""
        org = await org_factory()
        resp = await client.get(f"{ORGS}/{org.id}/discount-config", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["data"] is None

    @pytest.mark.asyncio
    async def test_get_includes_all_response_fields(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Each discount item has all expected schema fields."""
        config = _discount_config_input([_percentage_item(pricing_tier_ids[0])])
        org_id, _ = await _create_org(client, admin_headers, config)

        resp = await client.get(f"{ORGS}/{org_id}/discount-config", headers=admin_headers)
        item = resp.json()["data"]["discounts"][0]
        for field in (
            "id", "organization_id", "service_tier_id", "discount_type",
            "is_enabled", "value", "valid_from", "valid_until", "volume_tiers",
            "created_at", "updated_at", "version",
        ):
            assert field in item, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_get_volume_tiered_response(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """VOLUME_TIERED item has volume_tiers populated and value=null."""
        config = _discount_config_input([_volume_item(pricing_tier_ids[0])])
        org_id, _ = await _create_org(client, admin_headers, config)

        resp = await client.get(f"{ORGS}/{org_id}/discount-config", headers=admin_headers)
        item = resp.json()["data"]["discounts"][0]
        assert item["volume_tiers"] is not None
        assert item["value"] is None

    @pytest.mark.asyncio
    async def test_get_unknown_org_returns_404(
        self, client: AsyncClient, admin_headers: dict
    ) -> None:
        """Unknown org ID → 404."""
        resp = await client.get(
            f"{ORGS}/00000000-0000-0000-0000-000000000000/discount-config",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_requires_auth(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """No auth → 401."""
        org_id, _ = await _create_org(client, admin_headers, _discount_config_input([_percentage_item(pricing_tier_ids[0])]))
        resp = await client.get(
            f"{ORGS}/{org_id}/discount-config",
            headers={"X-Client-Type": "ADMIN"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_requires_admin(
        self, client: AsyncClient, admin_headers: dict, auth_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Non-admin → 403."""
        org_id, _ = await _create_org(client, admin_headers, _discount_config_input([_percentage_item(pricing_tier_ids[0])]))
        resp = await client.get(f"{ORGS}/{org_id}/discount-config", headers=auth_headers)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
#  PUT (upsert)
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpsertDiscountConfig:
    """PUT /v1/organizations/{id}/discount-config — create-or-replace, reason mandatory."""

    @pytest.mark.asyncio
    async def test_upsert_creates_new_config(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str], org_factory
    ) -> None:
        """PUT on org with no existing config creates all rows."""
        org = await org_factory()
        payload = _discount_config_upsert([_percentage_item(pricing_tier_ids[0])])

        resp = await client.put(
            f"{ORGS}/{org.id}/discount-config",
            json=payload,
            headers=admin_headers,
        )
        assert resp.status_code == 200
        dc = resp.json()["data"]
        assert dc["organization_id"] == org.id
        assert len(dc["discounts"]) == 1
        assert dc["discounts"][0]["discount_type"] == "PERCENTAGE"

    @pytest.mark.asyncio
    async def test_upsert_updates_existing_row(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """PUT with the same (tier, type) updates the existing row."""
        config = _discount_config_input([_percentage_item(pricing_tier_ids[0], "10.00")])
        org_id, _ = await _create_org(client, admin_headers, config)

        # Upsert same tier/type with a different value
        payload = _discount_config_upsert(
            [_percentage_item(pricing_tier_ids[0], "20.00")],
            reason="Increasing percentage discount",
        )
        resp = await client.put(
            f"{ORGS}/{org_id}/discount-config",
            json=payload,
            headers=admin_headers,
        )
        assert resp.status_code == 200
        item = resp.json()["data"]["discounts"][0]
        assert Decimal(item["value"]) == Decimal("20.00")

    @pytest.mark.asyncio
    async def test_upsert_adds_new_tier_type_combinations(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Upserting includes new (tier, type) rows alongside existing ones."""
        config = _discount_config_input([_percentage_item(pricing_tier_ids[0])])
        org_id, _ = await _create_org(client, admin_headers, config)

        payload = _discount_config_upsert([
            _percentage_item(pricing_tier_ids[0], "15.00"),
            _fixed_item(pricing_tier_ids[1], "8.00"),
            _volume_item(pricing_tier_ids[0]),
        ])
        resp = await client.put(
            f"{ORGS}/{org_id}/discount-config",
            json=payload,
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]["discounts"]) == 3

    @pytest.mark.asyncio
    async def test_upsert_can_disable_discount(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Upserting with is_enabled=False disables the discount without removing it."""
        config = _discount_config_input([_percentage_item(pricing_tier_ids[0], enabled=True)])
        org_id, _ = await _create_org(client, admin_headers, config)

        payload = _discount_config_upsert(
            [_percentage_item(pricing_tier_ids[0], enabled=False)],
            reason="Temporarily disabling discount",
        )
        resp = await client.put(
            f"{ORGS}/{org_id}/discount-config",
            json=payload,
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["discounts"][0]["is_enabled"] is False

    @pytest.mark.asyncio
    async def test_upsert_all_three_discount_types_for_one_tier(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str], org_factory
    ) -> None:
        """A single tier can have PERCENTAGE, FIXED_PER_BOOKING, and VOLUME_TIERED simultaneously."""
        org = await org_factory()
        payload = _discount_config_upsert([
            _percentage_item(pricing_tier_ids[0]),
            _fixed_item(pricing_tier_ids[0]),
            _volume_item(pricing_tier_ids[0]),
        ])
        resp = await client.put(
            f"{ORGS}/{org.id}/discount-config",
            json=payload,
            headers=admin_headers,
        )
        assert resp.status_code == 200
        types = {d["discount_type"] for d in resp.json()["data"]["discounts"]}
        assert types == {"PERCENTAGE", "FIXED_PER_BOOKING", "VOLUME_TIERED"}

    # ── Validation errors ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_upsert_missing_reason_returns_422(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str], org_factory
    ) -> None:
        """reason field is mandatory for PUT → 422 when omitted."""
        org = await org_factory()
        payload = {"discounts": [_percentage_item(pricing_tier_ids[0])]}  # no reason
        resp = await client.put(
            f"{ORGS}/{org.id}/discount-config",
            json=payload,
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_upsert_reason_too_short_returns_422(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str], org_factory
    ) -> None:
        """reason shorter than 3 chars → 422."""
        org = await org_factory()
        payload = _discount_config_upsert([_percentage_item(pricing_tier_ids[0])], reason="ab")
        resp = await client.put(
            f"{ORGS}/{org.id}/discount-config",
            json=payload,
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_upsert_duplicate_tier_type_returns_422(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str], org_factory
    ) -> None:
        """Duplicate (service_tier_id, discount_type) in same request → 422."""
        org = await org_factory()
        payload = _discount_config_upsert([
            _percentage_item(pricing_tier_ids[0]),
            _percentage_item(pricing_tier_ids[0]),  # duplicate
        ])
        resp = await client.put(
            f"{ORGS}/{org.id}/discount-config",
            json=payload,
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_upsert_invalid_percentage_value_returns_422(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str], org_factory
    ) -> None:
        """PERCENTAGE with value > 100 → 422."""
        org = await org_factory()
        bad = _percentage_item(pricing_tier_ids[0], value="110.00")
        resp = await client.put(
            f"{ORGS}/{org.id}/discount-config",
            json=_discount_config_upsert([bad]),
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_upsert_volume_tiers_gap_returns_422(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str], org_factory
    ) -> None:
        """Volume tiers with a gap in booking ranges → 422."""
        org = await org_factory()
        bad = {
            "service_tier_id": pricing_tier_ids[0],
            "discount_type": "VOLUME_TIERED",
            "volume_tiers": [
                {"min_bookings": 1, "max_bookings": 50, "discount_pct": "5.00"},
                {"min_bookings": 100, "max_bookings": None, "discount_pct": "15.00"},  # gap 51-99
            ],
        }
        resp = await client.put(
            f"{ORGS}/{org.id}/discount-config",
            json=_discount_config_upsert([bad]),
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_upsert_unknown_org_returns_404(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Unknown org ID → 404."""
        payload = _discount_config_upsert([_percentage_item(pricing_tier_ids[0])])
        resp = await client.put(
            f"{ORGS}/00000000-0000-0000-0000-000000000000/discount-config",
            json=payload,
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_upsert_requires_admin(
        self, client: AsyncClient, admin_headers: dict, auth_headers: dict,
        pricing_tier_ids: list[str], org_factory
    ) -> None:
        """Non-admin → 403."""
        org = await org_factory()
        payload = _discount_config_upsert([_percentage_item(pricing_tier_ids[0])])
        resp = await client.put(
            f"{ORGS}/{org.id}/discount-config",
            json=payload,
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_upsert_requires_auth(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str], org_factory
    ) -> None:
        """No auth → 401."""
        org = await org_factory()
        payload = _discount_config_upsert([_percentage_item(pricing_tier_ids[0])])
        resp = await client.put(
            f"{ORGS}/{org.id}/discount-config",
            json=payload,
            headers={"X-Client-Type": "ADMIN"},
        )
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
#  DELETE
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeleteDiscountConfig:
    """DELETE /v1/organizations/{id}/discount-config — hard-delete all rows, admin only."""

    @pytest.mark.asyncio
    async def test_admin_can_delete_discount_config(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Admin deletes all discount rows; subsequent GET returns null."""
        config = _discount_config_input([
            _percentage_item(pricing_tier_ids[0]),
            _fixed_item(pricing_tier_ids[1]),
        ])
        org_id, _ = await _create_org(client, admin_headers, config)

        resp = await client.delete(f"{ORGS}/{org_id}/discount-config", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        get_resp = await client.get(f"{ORGS}/{org_id}/discount-config", headers=admin_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["data"] is None

    @pytest.mark.asyncio
    async def test_delete_removes_all_rows(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Deleting removes ALL discount rows for the org (not just one)."""
        config = _discount_config_input([
            _percentage_item(pricing_tier_ids[0]),
            _fixed_item(pricing_tier_ids[0]),
            _volume_item(pricing_tier_ids[1]),
        ])
        org_id, dc = await _create_org(client, admin_headers, config)
        assert len(dc["discounts"]) == 3

        await client.delete(f"{ORGS}/{org_id}/discount-config", headers=admin_headers)

        get_resp = await client.get(f"{ORGS}/{org_id}/discount-config", headers=admin_headers)
        assert get_resp.json()["data"] is None

    @pytest.mark.asyncio
    async def test_delete_not_found_when_no_config(
        self, client: AsyncClient, admin_headers: dict, org_factory
    ) -> None:
        """Deleting on org with no discount config → 404."""
        org = await org_factory()
        resp = await client.delete(f"{ORGS}/{org.id}/discount-config", headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_can_recreate_after_delete(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """After delete, a fresh upsert creates a clean config."""
        config = _discount_config_input([_percentage_item(pricing_tier_ids[0], "10.00")])
        org_id, _ = await _create_org(client, admin_headers, config)

        await client.delete(f"{ORGS}/{org_id}/discount-config", headers=admin_headers)

        payload = _discount_config_upsert(
            [_percentage_item(pricing_tier_ids[0], "25.00")],
            reason="Fresh config after delete",
        )
        re_resp = await client.put(
            f"{ORGS}/{org_id}/discount-config",
            json=payload,
            headers=admin_headers,
        )
        assert re_resp.status_code == 200
        item = re_resp.json()["data"]["discounts"][0]
        assert Decimal(item["value"]) == Decimal("25.00")

    @pytest.mark.asyncio
    async def test_delete_unknown_org_returns_404(
        self, client: AsyncClient, admin_headers: dict
    ) -> None:
        """Unknown org ID → 404."""
        resp = await client.delete(
            f"{ORGS}/00000000-0000-0000-0000-000000000000/discount-config",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_requires_admin(
        self, client: AsyncClient, admin_headers: dict, auth_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """Non-admin → 403."""
        config = _discount_config_input([_percentage_item(pricing_tier_ids[0])])
        org_id, _ = await _create_org(client, admin_headers, config)

        resp = await client.delete(f"{ORGS}/{org_id}/discount-config", headers=auth_headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_requires_auth(
        self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]
    ) -> None:
        """No auth → 401."""
        config = _discount_config_input([_percentage_item(pricing_tier_ids[0])])
        org_id, _ = await _create_org(client, admin_headers, config)

        resp = await client.delete(
            f"{ORGS}/{org_id}/discount-config",
            headers={"X-Client-Type": "ADMIN"},
        )
        assert resp.status_code == 401
