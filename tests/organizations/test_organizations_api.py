"""Integration API tests — Organization CRUD endpoints.

Covers:
- POST   /v1/organizations          create org + contacts
- GET    /v1/organizations          list with pagination, search, filter
- GET    /v1/organizations/{id}     read single
- PATCH  /v1/organizations/{id}     update (with mandatory reason)
- PATCH  /v1/organizations/{id}/status  status transitions
- DELETE /v1/organizations/{id}     soft delete
- PATCH  /v1/organizations/{id}/logo    upload org logo
- GET    /v1/organizations/{id}/account-manager   get assigned manager
- PATCH  /v1/organizations/{id}/account-manager   assign / unassign manager
- GET    /v1/organizations/account-managers        list eligible managers

All tests use per-test transaction rollback (no persistent state).
Arq background jobs are mocked so no Redis/worker is needed.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.organizations.models import Organization
from app.modules.service_tiers.enums import ServiceTierScopeType, ServiceTierStatus
from app.modules.service_tiers.models import ServiceTier
from app.modules.user.models import User

ORGS = "/v1/organizations"
DRAFTS = "/v1/organizations/drafts"


# ── Shared valid payload ───────────────────────────────────────────────────────


def _org_form_data(
    email: str | None = None,
    contacts: list | None = None,
    **overrides,
) -> dict:
    """Build flat multipart form data for org creation (multipart/form-data endpoint).

    Complex fields (contacts, pricing_plans, etc.) are JSON strings as required
    by the form endpoint. Use ``overrides`` to set or override any scalar field,
    or to inject JSON-string fields like ``pricing_plans``.
    """
    _contacts = contacts or [
        {
            "email": email or f"contact-{uuid.uuid4().hex[:8]}@acme.com",
            "first_name": "Jane",
            "last_name": "Doe",
            "contact_number": "+447700900000",
            "contact_role": "ACCOUNT_OWNER",
        }
    ]
    data = {
        "trading_name": "Acme Logistics Ltd",
        "legal_entity_name": "Acme Logistics Limited",
        "companies_house_number": "12345678",
        "vat_number": "GB123456789",
        "date_of_incorporation": "2015-06-01",
        "industry": "LOGISTICS_TRANSPORT",
        "company_size": "11-50 employees",
        "reg_address_line_1": "1 Acme Street",
        "reg_city": "London",
        "reg_postcode": "EC1A 1BB",
        "notes": "Key account",
        "contacts": json.dumps(_contacts),
    }
    data.update(overrides)
    return data


def _mock_enqueue():
    """Patch Arq enqueue so tests don't need Redis."""
    return patch("app.modules.organizations.service.enqueue", new_callable=AsyncMock, return_value=None)


def _mock_create_invite():
    """Patch AuthService.create_invite to avoid full invite machinery."""
    from app.modules.auth.service import CreateInviteResult

    fake_invite = MagicMock()
    fake_invite.id = "invite-id-fake"
    fake_user = MagicMock()
    return patch(
        "app.modules.organizations.service.AuthService.create_invite",
        new_callable=AsyncMock,
        return_value=CreateInviteResult(False, fake_invite, "raw-token-abc123", fake_user, "invite-id-fake"),
    )


# ═══════════════════════════════════════════════════
#  CREATE
# ═══════════════════════════════════════════════════


class TestCreateOrganization:
    """POST /v1/organizations — create org + B2B contacts."""

    @pytest.mark.asyncio
    async def test_create_success(self, client: AsyncClient, admin_headers: dict) -> None:
        """Admin can create an org; response contains reference, contacts, and invite token."""
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(), headers=admin_headers)

        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        org = data["organization"]
        assert org["trading_name"] == "Acme Logistics Ltd"
        assert org["reference"].startswith("SWC-ORG-")
        assert org["status"] == "ACTIVE"
        assert len(data["contacts"]) == 1
        assert data["contacts"][0]["invite_token"] == "raw-token-abc123"
        assert data["payment_config"] is None  # no config provided in this payload

    @pytest.mark.asyncio
    async def test_create_response_includes_pricing_plans(self, client: AsyncClient, admin_headers: dict) -> None:
        """pricing_plans field is present in org response (nullable)."""
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(), headers=admin_headers)

        assert resp.status_code == 201, resp.text
        org = resp.json()["data"]["organization"]
        assert "pricing_plans" in org

    @pytest.mark.asyncio
    async def test_create_with_pricing_plans(self, client: AsyncClient, admin_headers: dict, pricing_tier_ids: list[str]) -> None:
        """pricing_plans list is stored and returned as-is."""
        plans = [
            {
                "selected": True,
                "plain_name": "Fastest",
                "price_per_package": 22,
                "icon": "speed",
                "color": "red",
                "id_price_tier": pricing_tier_ids[0],
                "days": 3,
                "plain_type": "standard",
            },
            {
                "selected": False,
                "plain_name": "Economy",
                "price_per_package": 8,
                "icon": "eco",
                "color": "green",
                "id_price_tier": pricing_tier_ids[1],
                "days": 8,
                "plain_type": "custom",
            },
        ]
        data = _org_form_data(pricing_plans=json.dumps(plans))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)

        assert resp.status_code == 201, resp.text
        org = resp.json()["data"]["organization"]
        assert len(org["pricing_plans"]) == 2
        assert org["pricing_plans"][0]["plain_name"] == "Fastest"
        assert org["pricing_plans"][1]["plain_type"] == "custom"

    @pytest.mark.asyncio
    async def test_create_multiple_contacts(self, client: AsyncClient, admin_headers: dict) -> None:
        """Multiple contacts can be created at once, each gets an invite token."""
        contacts = [
            {"email": "owner@multi.com", "first_name": "Alice", "last_name": "Smith", "contact_number": "+441111111111", "contact_role": "ACCOUNT_OWNER"},
            {"email": "billing@multi.com", "first_name": "Bob", "last_name": "Jones", "contact_number": "+442222222222", "contact_role": "BILLING"},
        ]
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(contacts=contacts), headers=admin_headers)

        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert len(data["contacts"]) == 2

    @pytest.mark.asyncio
    async def test_create_requires_account_owner_contact(self, client: AsyncClient, admin_headers: dict) -> None:
        """At least one contact must have role ACCOUNT_OWNER → 422 otherwise."""
        contacts = [
            {"email": "billing@test.com", "first_name": "Bill", "last_name": "Pay", "contact_number": "+440000000000", "contact_role": "BILLING"},
        ]
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(contacts=contacts), headers=admin_headers)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_duplicate_contact_email_returns_409(self, client: AsyncClient, admin_headers: dict, verified_user: User) -> None:
        """Creating an org with an already-registered email raises 409."""
        contacts = [
            {"email": verified_user.email, "first_name": "Test", "last_name": "User", "contact_number": "+440000000001", "contact_role": "ACCOUNT_OWNER"},
        ]
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(contacts=contacts), headers=admin_headers)

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_create_requires_admin(self, client: AsyncClient, auth_headers: dict) -> None:
        """Non-admin (CUSTOMER_B2C) gets 403."""
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(), headers=auth_headers)

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_unauthenticated_returns_401(self, client: AsyncClient) -> None:
        """No auth header → 401."""
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(), headers={"X-Client-Type": "ADMIN"})

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_create_trading_name_too_short_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """trading_name shorter than 2 chars fails validation."""
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(trading_name="X"), headers=admin_headers)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_missing_required_fields_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """Missing companies_house_number → 422."""
        data = _org_form_data()
        del data["companies_house_number"]
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)

        assert resp.status_code == 422


# ═══════════════════════════════════════════════════
#  LIST
# ═══════════════════════════════════════════════════


class TestListOrganizations:
    """GET /v1/organizations — paginated list with search and filter."""

    @pytest.mark.asyncio
    async def test_list_returns_paginated_response(self, client: AsyncClient, admin_headers: dict, org_factory) -> None:
        """List endpoint returns paginated envelope."""
        await org_factory(trading_name="Alpha Corp", reference="SWC-ORG-00101")
        await org_factory(trading_name="Beta Corp", reference="SWC-ORG-00102")

        resp = await client.get(ORGS, headers=admin_headers)

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert "items" in body
        assert "total" in body
        assert "page" in body
        assert "pages" in body
        assert body["total"] >= 2

    @pytest.mark.asyncio
    async def test_list_search_by_trading_name(self, client: AsyncClient, admin_headers: dict, org_factory) -> None:
        """Search filters by trading_name (case-insensitive)."""
        await org_factory(trading_name="Unique Widgets Ltd", reference="SWC-ORG-00201")
        await org_factory(trading_name="Other Company", reference="SWC-ORG-00202")

        resp = await client.get(ORGS, params={"search": "unique widgets"}, headers=admin_headers)

        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) >= 1
        assert any("Unique Widgets" in i["trading_name"] for i in items)

    @pytest.mark.asyncio
    async def test_list_search_by_legal_entity_name(self, client: AsyncClient, admin_headers: dict, org_factory) -> None:
        """Search finds org by legal_entity_name."""
        await org_factory(
            trading_name="Trade Co",
            legal_entity_name="Legal Entity Unique Name Limited",
            reference="SWC-ORG-00203",
        )

        resp = await client.get(ORGS, params={"search": "Legal Entity Unique"}, headers=admin_headers)

        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert any("Legal Entity Unique" in i["legal_entity_name"] for i in items)

    @pytest.mark.asyncio
    async def test_list_search_by_reference(self, client: AsyncClient, admin_headers: dict, org_factory) -> None:
        """Search finds org by reference code."""
        await org_factory(trading_name="Ref Search Corp", reference="SWC-ORG-09999")

        resp = await client.get(ORGS, params={"search": "SWC-ORG-09999"}, headers=admin_headers)

        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert any(i["reference"] == "SWC-ORG-09999" for i in items)

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, client: AsyncClient, admin_headers: dict, org_factory) -> None:
        """Status filter returns only orgs with the requested status."""
        await org_factory(trading_name="Active Org", reference="SWC-ORG-00301", status="ACTIVE")
        await org_factory(trading_name="Inactive Org", reference="SWC-ORG-00302", status="INACTIVE")

        resp = await client.get(ORGS, params={"status": "INACTIVE"}, headers=admin_headers)

        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert all(i["status"] == "INACTIVE" for i in items)

    @pytest.mark.asyncio
    async def test_list_excludes_draft_orgs(self, client: AsyncClient, admin_headers: dict) -> None:
        """DRAFT orgs appear only on GET /organizations/drafts, not the main list."""
        suffix = uuid.uuid4().hex[:8]
        trading_name = f"Draft Exclude Test {suffix}"
        create = await client.post(DRAFTS, data={"trading_name": trading_name}, headers=admin_headers)
        assert create.status_code == 201, create.text
        data = create.json()["data"]
        org_id = data["id"]
        draft_number = data["draft_number"]

        list_resp = await client.get(ORGS, headers=admin_headers)
        assert list_resp.status_code == 200
        items = list_resp.json()["data"]["items"]
        assert not any(i["id"] == org_id for i in items)
        assert not any(i.get("trading_name") == trading_name for i in items)

        drafts_resp = await client.get(DRAFTS, params={"search": suffix}, headers=admin_headers)
        assert drafts_resp.status_code == 200
        draft_items = drafts_resp.json()["data"]["items"]
        assert any(it.get("draft_number") == draft_number for it in draft_items)

    @pytest.mark.asyncio
    async def test_list_pagination(self, client: AsyncClient, admin_headers: dict, org_factory) -> None:
        """Pagination returns correct page size."""
        for i in range(5):
            await org_factory(reference=f"SWC-ORG-0{i:04d}")

        resp = await client.get(ORGS, params={"page": 1, "size": 2}, headers=admin_headers)

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert len(body["items"]) <= 2
        assert body["size"] == 2

    @pytest.mark.asyncio
    async def test_list_requires_admin(self, client: AsyncClient, auth_headers: dict) -> None:
        """Non-admin gets 403."""
        resp = await client.get(ORGS, headers=auth_headers)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  READ SINGLE
# ═══════════════════════════════════════════════════


class TestGetOrganization:
    """GET /v1/organizations/{id} — read single org."""

    @pytest.mark.asyncio
    async def test_get_returns_full_profile(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """Returns the org with all fields including reference, pricing_plans, and version."""
        resp = await client.get(f"{ORGS}/{sample_org.id}", headers=admin_headers)

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == sample_org.id
        assert data["trading_name"] == sample_org.trading_name
        assert data["legal_entity_name"] == sample_org.legal_entity_name
        assert data["companies_house_number"] == sample_org.companies_house_number
        assert data["vat_number"] == sample_org.vat_number
        assert "reference" in data
        assert "pricing_plans" in data
        assert "version" in data
        assert "created_at" in data
        assert "updated_at" in data

    @pytest.mark.asyncio
    async def test_get_not_found_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        """Unknown ID → 404."""
        resp = await client.get(f"{ORGS}/00000000-0000-0000-0000-000000000000", headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_requires_admin(self, client: AsyncClient, auth_headers: dict, sample_org: Organization) -> None:
        """Non-admin gets 403."""
        resp = await client.get(f"{ORGS}/{sample_org.id}", headers=auth_headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_super_admin_can_get_org_without_org_contact(
        self,
        client: AsyncClient,
        user_factory,
        sample_org: Organization,
    ) -> None:
        """SUPER_ADMIN bypasses org_contact membership (same as ADMIN)."""
        super_admin = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)
        token, _ = create_access_token(
            user_id=super_admin.id,
            role=super_admin.role,
            client_type="ADMIN",
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Client-Type": "ADMIN",
        }
        resp = await client.get(f"{ORGS}/{sample_org.id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == sample_org.id


# ═══════════════════════════════════════════════════
#  UPDATE
# ═══════════════════════════════════════════════════


class TestUpdateOrganization:
    """PATCH /v1/organizations/{id} — update org fields."""

    @pytest.mark.asyncio
    async def test_update_trading_name(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """Admin can update trading_name."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}",
            json={"trading_name": "Updated Corp", "reason": "Rebranding"},
            headers=admin_headers,
        )

        assert resp.status_code == 200
        data = resp.json()["data"]["organization"]
        assert data["trading_name"] == "Updated Corp"

    @pytest.mark.asyncio
    async def test_update_pricing_plans(self, client: AsyncClient, admin_headers: dict, sample_org: Organization, pricing_tier_ids: list[str]) -> None:
        """Admin can update pricing_plans JSON."""
        plans = [
            {
                "plain_name": "Fastest",
                "price_per_package": 20,
                "plain_type": "standard",
                "selected": True,
                "days": 3,
                "id_price_tier": pricing_tier_ids[0],
            }
        ]
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}",
            json={"pricing_plans": plans, "reason": "Assigning pricing plans"},
            headers=admin_headers,
        )

        assert resp.status_code == 200
        data = resp.json()["data"]["organization"]
        assert data["pricing_plans"][0]["plain_name"] == "Fastest"

    @pytest.mark.asyncio
    async def test_update_requires_reason(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """Missing reason field → 422."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}",
            json={"trading_name": "No Reason Corp"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_reason_too_short_returns_422(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """Reason shorter than 3 chars → 422."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}",
            json={"trading_name": "Corp", "reason": "ab"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_not_found_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        """Updating unknown org → 404."""
        resp = await client.patch(
            f"{ORGS}/00000000-0000-0000-0000-000000000000",
            json={"trading_name": "Ghost Corp", "reason": "Fixing ghost data"},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_requires_admin(self, client: AsyncClient, auth_headers: dict, sample_org: Organization) -> None:
        """Non-admin gets 403."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}",
            json={"trading_name": "Hack Corp", "reason": "Trying to hack"},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_update_partial_fields_does_not_overwrite_others(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """Unset optional fields are not overwritten (partial update)."""
        original_trading_name = sample_org.trading_name
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}",
            json={"vat_number": "GB999999999", "reason": "Updating VAT number"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]["organization"]
        assert data["trading_name"] == original_trading_name
        assert data["vat_number"] == "GB999999999"

    @pytest.mark.asyncio
    async def test_update_registered_address(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """Registered address can be updated via nested object."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}",
            json={
                "registered_address": {
                    "address_line_1": "99 New Street",
                    "city": "Manchester",
                    "postcode": "M1 1AA",
                },
                "reason": "Office relocation",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]["organization"]
        assert data["reg_address_line_1"] == "99 New Street"
        assert data["reg_city"] == "Manchester"
        assert data["reg_postcode"] == "M1 1AA"

    @pytest.mark.asyncio
    async def test_update_response_payment_config_null_when_no_config(
        self, client: AsyncClient, admin_headers: dict, sample_org: Organization
    ) -> None:
        """PATCH /{org_id} response includes payment_config=null when no config exists."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}",
            json={"trading_name": "New Name Ltd", "reason": "Rebranding"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "organization" in data
        assert "payment_config" in data
        assert data["payment_config"] is None

    @pytest.mark.asyncio
    async def test_update_response_includes_payment_config_when_exists(
        self, client: AsyncClient, admin_headers: dict, sample_org: Organization
    ) -> None:
        """PATCH /{org_id} response includes full payment_config (with methods) when it exists."""
        # First create payment config for the org
        config_payload = {
            "vat_rate": "STANDARD_20",
            "vat_treatment": "UK",
            "max_delivery_attempts": 2,
            "delivery_attempt_fees": [{"attempt": 1, "fee": "0.00"}, {"attempt": 2, "fee": "2.50"}],
            "max_return_attempts": 2,
            "return_attempt_fees": [{"attempt": 1, "fee": "5.00"}, {"attempt": 2, "fee": "8.00"}],
            "payment_methods": [
                {"payment_model": "CARD", "billing_schedule": "IMMEDIATE", "is_default": True}
            ],
        }
        await client.post(f"{ORGS}/{sample_org.id}/payment-config", json=config_payload, headers=admin_headers)

        # Now PATCH org — response must include the existing payment_config with methods
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}",
            json={"trading_name": "Updated Name Ltd", "reason": "Rebranding"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        pc = data["payment_config"]
        assert pc is not None
        assert pc["vat_rate"] == "STANDARD_20"
        assert len(pc["payment_methods"]) == 1
        assert pc["payment_methods"][0]["payment_model"] == "CARD"
        assert pc["payment_methods"][0]["is_default"] is True

    @pytest.mark.asyncio
    async def test_update_with_embedded_payment_config(
        self, client: AsyncClient, admin_headers: dict, sample_org: Organization
    ) -> None:
        """PATCH /{org_id} with embedded payment_config updates shared fields atomically."""
        # Create initial payment config
        config_payload = {
            "vat_rate": "STANDARD_20",
            "vat_treatment": "UK",
            "max_delivery_attempts": 2,
            "delivery_attempt_fees": [{"attempt": 1, "fee": "0.00"}, {"attempt": 2, "fee": "2.50"}],
            "max_return_attempts": 2,
            "return_attempt_fees": [{"attempt": 1, "fee": "5.00"}, {"attempt": 2, "fee": "8.00"}],
            "payment_methods": [
                {"payment_model": "CARD", "billing_schedule": "IMMEDIATE", "is_default": True}
            ],
        }
        await client.post(f"{ORGS}/{sample_org.id}/payment-config", json=config_payload, headers=admin_headers)

        # PATCH org + embedded payment_config update in one request
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}",
            json={
                "trading_name": "Rebranded Ltd",
                "payment_config": {
                    "vat_rate": "REDUCED_5",
                    "weight_margin_kg": 2.5,
                    "weight_surcharge_per_kg": "3.00",
                },
                "reason": "Annual review update",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["organization"]["trading_name"] == "Rebranded Ltd"
        pc = data["payment_config"]
        assert pc is not None
        assert pc["vat_rate"] == "REDUCED_5"
        assert float(pc["weight_margin_kg"]) == 2.5
        assert pc["weight_surcharge_per_kg"] == "3.00"
        # payment methods must still be present (not wiped by embedded update)
        assert len(pc["payment_methods"]) == 1
        assert pc["payment_methods"][0]["payment_model"] == "CARD"

    @pytest.mark.asyncio
    async def test_update_embedded_payment_config_without_existing_config_creates_it(
        self, client: AsyncClient, admin_headers: dict, sample_org: Organization
    ) -> None:
        """Embedded payment_config update on org with no config auto-creates shared config."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}",
            json={
                "payment_config": {"vat_rate": "ZERO_RATED"},
                "reason": "Updating VAT",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["payment_config"] is not None
        assert data["payment_config"]["vat_rate"] == "ZERO_RATED"

    @pytest.mark.asyncio
    async def test_update_embedded_payment_config_mismatched_attempt_fees_returns_422(
        self, client: AsyncClient, admin_headers: dict, sample_org: Organization
    ) -> None:
        """Embedded payment_config: delivery_attempt_fees count mismatch → 422."""
        # Create initial config with 3 delivery attempts
        config_payload = {
            "vat_rate": "STANDARD_20",
            "vat_treatment": "UK",
            "max_delivery_attempts": 3,
            "delivery_attempt_fees": [
                {"attempt": 1, "fee": "0.00"},
                {"attempt": 2, "fee": "2.00"},
                {"attempt": 3, "fee": "4.00"},
            ],
            "max_return_attempts": 2,
            "return_attempt_fees": [{"attempt": 1, "fee": "5.00"}, {"attempt": 2, "fee": "8.00"}],
            "payment_methods": [
                {"payment_model": "CARD", "billing_schedule": "IMMEDIATE", "is_default": True}
            ],
        }
        await client.post(f"{ORGS}/{sample_org.id}/payment-config", json=config_payload, headers=admin_headers)

        # Update with wrong number of attempt fees (2 fees but max_delivery_attempts=3)
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}",
            json={
                "payment_config": {
                    "max_delivery_attempts": 3,
                    "delivery_attempt_fees": [
                        {"attempt": 1, "fee": "0.00"},
                        {"attempt": 2, "fee": "2.00"},
                    ],
                },
                "reason": "Updating attempt fees",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_pricing_plans_rejects_org_scoped_tier_id(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org: Organization,
        db_session: AsyncSession,
    ) -> None:
        """pricing_plans id_price_tier must point to GLOBAL tiers only."""
        org_scoped_tier = ServiceTier(
            tier_name="Custom Express",
            duration_days=1,
            error_margin_kg=0,
            price_per_kg="0.00",
            price_per_package="11.00",
            base_price="0.00",
            scope_type=ServiceTierScopeType.ORG.value,
            scope_org_id=sample_org.id,
            available_for="CUSTOMER_B2B",
            status=ServiceTierStatus.ACTIVE,
        )
        db_session.add(org_scoped_tier)
        await db_session.flush()

        payload = {
            "pricing_plans": [
                {
                    "plain_name": "Bad Custom",
                    "price_per_package": "11.00",
                    "plain_type": "custom",
                    "selected": True,
                    "days": 1,
                    "id_price_tier": org_scoped_tier.id,
                }
            ],
            "reason": "Try invalid org tier reference",
        }
        resp = await client.patch(f"{ORGS}/{sample_org.id}", json=payload, headers=admin_headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_pricing_plans_rejects_superfast_deselect(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org: Organization,
        db_session: AsyncSession,
    ) -> None:
        from app.modules.service_tiers.constants import SUPERFAST_TIER_NAME
        from app.modules.service_tiers.repository import ServiceTierRepository

        repo = ServiceTierRepository(db_session)
        superfast = await repo.find_global_superfast()
        assert superfast is not None

        payload = {
            "pricing_plans": [
                {
                    "plain_name": SUPERFAST_TIER_NAME,
                    "price_per_package": "125.00",
                    "plain_type": "standard",
                    "selected": False,
                    "permitted": False,
                    "days": 1,
                    "id_price_tier": superfast.id,
                }
            ],
            "reason": "Try to deselect Superfast",
        }
        resp = await client.patch(f"{ORGS}/{sample_org.id}", json=payload, headers=admin_headers)
        assert resp.status_code == 422
        assert "Superfast" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_update_pricing_plans_auto_includes_superfast(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org: Organization,
        pricing_tier_ids: list[str],
        db_session: AsyncSession,
    ) -> None:
        from app.modules.service_tiers.constants import SUPERFAST_TIER_NAME
        from app.modules.service_tiers.repository import ServiceTierRepository

        superfast = await ServiceTierRepository(db_session).find_global_superfast()
        assert superfast is not None
        other_id = next(tid for tid in pricing_tier_ids if tid != superfast.id)

        plans = [
            {
                "plain_name": "Basic Plan",
                "price_per_package": "50.85",
                "plain_type": "standard",
                "selected": True,
                "permitted": True,
                "days": 30,
                "id_price_tier": other_id,
            }
        ]
        payload = {"pricing_plans": plans, "reason": "Assign pricing without Superfast"}
        resp = await client.patch(f"{ORGS}/{sample_org.id}", json=payload, headers=admin_headers)
        assert resp.status_code == 200, resp.text
        saved = resp.json()["data"]["organization"]["pricing_plans"]
        assert any(p.get("plain_name") == SUPERFAST_TIER_NAME and p.get("permitted") is True for p in saved)


class TestOrganizationStatusChange:
    """PATCH /v1/organizations/{id}/status — status transitions."""

    @pytest.mark.asyncio
    async def test_deactivate_active_org(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """active → inactive is a valid transition."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/status",
            json={"status": "INACTIVE", "reason": "Client offboarded"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "INACTIVE"

    @pytest.mark.asyncio
    async def test_suspend_active_org(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """active → suspended is a valid transition."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/status",
            json={"status": "SUSPENDED", "reason": "Payment overdue"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "SUSPENDED"

    @pytest.mark.asyncio
    async def test_reactivate_inactive_org(self, client: AsyncClient, admin_headers: dict, org_factory) -> None:
        """inactive → active is a valid transition."""
        org = await org_factory(status="INACTIVE", reference="SWC-ORG-08001")

        resp = await client.patch(
            f"{ORGS}/{org.id}/status",
            json={"status": "ACTIVE", "reason": "Client renewed contract"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_reactivate_suspended_org(self, client: AsyncClient, admin_headers: dict, org_factory) -> None:
        """suspended → active is a valid transition."""
        org = await org_factory(status="SUSPENDED", reference="SWC-ORG-08002")

        resp = await client.patch(
            f"{ORGS}/{org.id}/status",
            json={"status": "ACTIVE", "reason": "Payment received"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_invalid_transition_inactive_to_suspended(self, client: AsyncClient, admin_headers: dict, org_factory) -> None:
        """inactive → suspended is NOT allowed → 409."""
        org = await org_factory(status="INACTIVE", reference="SWC-ORG-08003")

        resp = await client.patch(
            f"{ORGS}/{org.id}/status",
            json={"status": "SUSPENDED", "reason": "Should not work"},
            headers=admin_headers,
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_status_change_requires_reason(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """Missing reason → 422."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/status",
            json={"status": "INACTIVE"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_status_change_not_found_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        """Unknown org → 404."""
        resp = await client.patch(
            f"{ORGS}/00000000-0000-0000-0000-000000000000/status",
            json={"status": "INACTIVE", "reason": "Ghost org"},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_status_change_requires_admin(self, client: AsyncClient, auth_headers: dict, sample_org: Organization) -> None:
        """Non-admin gets 403."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/status",
            json={"status": "INACTIVE", "reason": "Trying to deactivate"},
            headers=auth_headers,
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  DELETE (soft)
# ═══════════════════════════════════════════════════


class TestDeleteOrganization:
    """DELETE /v1/organizations/{id} — soft delete."""

    @pytest.mark.asyncio
    async def test_delete_sets_status_inactive(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """DELETE returns 200 and the org is deactivated."""
        resp = await client.delete(f"{ORGS}/{sample_org.id}", headers=admin_headers)

        assert resp.status_code == 200
        assert resp.json()["success"] is True

        get_resp = await client.get(f"{ORGS}/{sample_org.id}", headers=admin_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["status"] == "INACTIVE"

    @pytest.mark.asyncio
    async def test_delete_not_found_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        """Unknown org → 404."""
        resp = await client.delete(f"{ORGS}/00000000-0000-0000-0000-000000000000", headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_requires_admin(self, client: AsyncClient, auth_headers: dict, sample_org: Organization) -> None:
        """Non-admin gets 403."""
        resp = await client.delete(f"{ORGS}/{sample_org.id}", headers=auth_headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_unauthenticated_returns_401(self, client: AsyncClient, sample_org: Organization) -> None:
        """No auth → 401."""
        resp = await client.delete(f"{ORGS}/{sample_org.id}", headers={"X-Client-Type": "ADMIN"})
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════
#  TRADING ADDRESS
# ═══════════════════════════════════════════════════


class TestTradingAddress:
    """Trading address fields on org create / update / response."""

    @pytest.mark.asyncio
    async def test_create_with_trading_address(self, client: AsyncClient, admin_headers: dict) -> None:
        """Trading address fields are persisted and returned in create response."""
        data = _org_form_data(
            trading_address_line_1="10 Office Park",
            trading_address_line_2="Suite 5",
            trading_address_city="Manchester",
            trading_address_state="Greater Manchester",
            trading_address_postcode="M1 1AB",
            trading_address_country="United Kingdom",
        )
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)

        assert resp.status_code == 201, resp.text
        org = resp.json()["data"]["organization"]
        assert org["trading_address_line_1"] == "10 Office Park"
        assert org["trading_address_line_2"] == "Suite 5"
        assert org["trading_address_city"] == "Manchester"
        assert org["trading_address_postcode"] == "M1 1AB"
        assert org["trading_address_country"] == "United Kingdom"

    @pytest.mark.asyncio
    async def test_create_without_trading_address_returns_nulls(self, client: AsyncClient, admin_headers: dict) -> None:
        """When trading address is omitted all trading_address_* fields are null."""
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(), headers=admin_headers)

        assert resp.status_code == 201, resp.text
        org = resp.json()["data"]["organization"]
        assert org["trading_address_line_1"] is None
        assert org["trading_address_city"] is None
        assert org["trading_address_postcode"] is None

    @pytest.mark.asyncio
    async def test_update_trading_address(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """Admin can set / update the trading address via PATCH."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}",
            json={
                "trading_address": {
                    "address_line_1": "20 New Road",
                    "city": "Birmingham",
                    "postcode": "B1 1AA",
                },
                "reason": "Moving trading address",
            },
            headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        org = resp.json()["data"]["organization"]
        assert org["trading_address_line_1"] == "20 New Road"
        assert org["trading_address_city"] == "Birmingham"
        assert org["trading_address_postcode"] == "B1 1AA"


# ═══════════════════════════════════════════════════
#  PICKUP ADDRESSES — inline create
# ═══════════════════════════════════════════════════


class TestCreateOrgWithPickupAddresses:
    """pickup_addresses created atomically via POST /v1/organizations."""

    @pytest.mark.asyncio
    async def test_create_with_single_pickup_address(self, client: AsyncClient, admin_headers: dict) -> None:
        """A single pickup address is created and returned in the response."""
        pickup = [
            {
                "line_1": "45 Street Road",
                "city": "London",
                "state": "Greater London",
                "postcode": "W8 5ED",
                "country": "United Kingdom",
                "is_default": True,
                "latitude": 51.5007,
                "longitude": -0.1246,
            }
        ]
        data = _org_form_data(pickup_addresses=json.dumps(pickup))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)

        assert resp.status_code == 201, resp.text
        result = resp.json()["data"]
        assert result["pickup_addresses"] is not None
        assert len(result["pickup_addresses"]) == 1
        pa = result["pickup_addresses"][0]
        assert pa["line_1"] == "45 Street Road"
        assert pa["city"] == "London"
        assert pa["is_default"] is True
        assert pa["latitude"] == 51.5007
        assert pa["longitude"] == -0.1246

    @pytest.mark.asyncio
    async def test_create_with_multiple_pickup_addresses(self, client: AsyncClient, admin_headers: dict) -> None:
        """Multiple pickup addresses — only one is default."""
        pickup = [
            {
                "line_1": "Street 45",
                "city": "London",
                "state": "Greater London",
                "postcode": "W8 5ED",
                "country": "United Kingdom",
                "is_default": True,
            },
            {
                "line_1": "10 Office Park",
                "city": "Manchester",
                "state": "Greater Manchester",
                "postcode": "M1 1AB",
                "country": "United Kingdom",
                "is_default": False,
            },
        ]
        data = _org_form_data(pickup_addresses=json.dumps(pickup))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)

        assert resp.status_code == 201, resp.text
        addresses = resp.json()["data"]["pickup_addresses"]
        assert len(addresses) == 2
        defaults = [a for a in addresses if a["is_default"]]
        assert len(defaults) == 1
        assert defaults[0]["line_1"] == "Street 45"

    @pytest.mark.asyncio
    async def test_create_pickup_no_explicit_default_auto_sets_first(self, client: AsyncClient, admin_headers: dict) -> None:
        """If no address has is_default=true, the first entry is auto-promoted."""
        pickup = [
            {
                "line_1": "First Address",
                "city": "London",
                "state": "Greater London",
                "postcode": "W1A 1AA",
                "country": "United Kingdom",
                "is_default": False,
            },
            {
                "line_1": "Second Address",
                "city": "Leeds",
                "state": "Yorkshire",
                "postcode": "LS1 1AB",
                "country": "United Kingdom",
                "is_default": False,
            },
        ]
        data = _org_form_data(pickup_addresses=json.dumps(pickup))
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)

        assert resp.status_code == 201, resp.text
        addresses = resp.json()["data"]["pickup_addresses"]
        defaults = [a for a in addresses if a["is_default"]]
        assert len(defaults) == 1
        assert defaults[0]["line_1"] == "First Address"

    @pytest.mark.asyncio
    async def test_create_without_pickup_addresses_returns_null(self, client: AsyncClient, admin_headers: dict) -> None:
        """When pickup_addresses is omitted, response field is null."""
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=_org_form_data(), headers=admin_headers)

        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["pickup_addresses"] is None

    @pytest.mark.asyncio
    async def test_create_pickup_invalid_json_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """Malformed pickup_addresses JSON → 422."""
        data = _org_form_data(pickup_addresses="not-valid-json")
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(ORGS, data=data, headers=admin_headers)

        assert resp.status_code == 422


# ═══════════════════════════════════════════════════
#  PICKUP ADDRESSES — CRUD routes
# ═══════════════════════════════════════════════════


class TestPickupAddressCRUD:
    """GET/POST/PATCH/DELETE /v1/organizations/{id}/pickup-addresses."""

    @pytest.mark.asyncio
    async def test_list_empty_for_new_org(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """New org with no pickup addresses returns empty list."""
        resp = await client.get(f"{ORGS}/{sample_org.id}/pickup-addresses", headers=admin_headers)

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_create_pickup_address(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """POST creates a pickup address; is_default=true clears others."""
        resp = await client.post(
            f"{ORGS}/{sample_org.id}/pickup-addresses",
            json=[
                {
                    "line_1": "45 Street Road",
                    "line_2": "Apartment 43",
                    "city": "London",
                    "state": "Greater London",
                    "postcode": "W8 5ED",
                    "country": "United Kingdom",
                    "is_default": True,
                    "latitude": 51.5007,
                    "longitude": -0.1246,
                }
            ],
            headers=admin_headers,
        )

        assert resp.status_code == 201, resp.text
        pa = resp.json()["data"][0]
        assert pa["line_1"] == "45 Street Road"
        assert pa["is_default"] is True
        assert pa["latitude"] == 51.5007
        assert pa["longitude"] == -0.1246
        assert pa["organization_id"] == sample_org.id

    @pytest.mark.asyncio
    async def test_create_second_address_clears_previous_default(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """Setting a new address as default clears the old default."""
        await client.post(
            f"{ORGS}/{sample_org.id}/pickup-addresses",
            json=[
                {
                    "line_1": "First",
                    "city": "London",
                    "state": "Greater London",
                    "postcode": "W1A 1AA",
                    "country": "United Kingdom",
                    "is_default": True,
                }
            ],
            headers=admin_headers,
        )
        await client.post(
            f"{ORGS}/{sample_org.id}/pickup-addresses",
            json=[
                {
                    "line_1": "Second",
                    "city": "Leeds",
                    "state": "Yorkshire",
                    "postcode": "LS1 1AB",
                    "country": "United Kingdom",
                    "is_default": True,
                }
            ],
            headers=admin_headers,
        )

        resp = await client.get(f"{ORGS}/{sample_org.id}/pickup-addresses", headers=admin_headers)
        addresses = resp.json()["data"]
        defaults = [a for a in addresses if a["is_default"]]
        assert len(defaults) == 1
        assert defaults[0]["line_1"] == "Second"

    @pytest.mark.asyncio
    async def test_list_returns_oldest_created_first(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """List returns pickup addresses in creation order (oldest first)."""
        await client.post(
            f"{ORGS}/{sample_org.id}/pickup-addresses",
            json=[
                {
                    "line_1": "First Created",
                    "city": "London",
                    "state": "Greater London",
                    "postcode": "W1A 1AA",
                    "country": "United Kingdom",
                    "is_default": False,
                }
            ],
            headers=admin_headers,
        )
        await client.post(
            f"{ORGS}/{sample_org.id}/pickup-addresses",
            json=[
                {
                    "line_1": "Second Created",
                    "city": "Leeds",
                    "state": "Yorkshire",
                    "postcode": "LS1 1AB",
                    "country": "United Kingdom",
                    "is_default": True,
                }
            ],
            headers=admin_headers,
        )

        resp = await client.get(f"{ORGS}/{sample_org.id}/pickup-addresses", headers=admin_headers)
        addresses = resp.json()["data"]
        assert addresses[0]["line_1"] == "First Created"
        assert addresses[1]["line_1"] == "Second Created"
        assert addresses[1]["is_default"] is True

    @pytest.mark.asyncio
    async def test_update_pickup_address(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """PATCH updates the address fields."""
        create_resp = await client.post(
            f"{ORGS}/{sample_org.id}/pickup-addresses",
            json=[
                {
                    "line_1": "Old Street",
                    "city": "London",
                    "state": "Greater London",
                    "postcode": "W1A 1AA",
                    "country": "United Kingdom",
                    "is_default": True,
                }
            ],
            headers=admin_headers,
        )
        address_id = create_resp.json()["data"][0]["id"]

        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/pickup-addresses/{address_id}",
            json={"line_1": "New Street", "city": "Bristol", "state": "Bristol", "postcode": "BS1 1AA"},
            headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        pa = resp.json()["data"]
        assert pa["line_1"] == "New Street"
        assert pa["city"] == "Bristol"
        assert pa["postcode"] == "BS1 1AA"

    @pytest.mark.asyncio
    async def test_update_set_as_default(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """PATCH is_default=true promotes the address and clears the old default."""
        r1 = await client.post(
            f"{ORGS}/{sample_org.id}/pickup-addresses",
            json=[
                {
                    "line_1": "First",
                    "city": "London",
                    "state": "Greater London",
                    "postcode": "W1A 1AA",
                    "country": "United Kingdom",
                    "is_default": True,
                }
            ],
            headers=admin_headers,
        )
        r2 = await client.post(
            f"{ORGS}/{sample_org.id}/pickup-addresses",
            json=[
                {
                    "line_1": "Second",
                    "city": "Leeds",
                    "state": "Yorkshire",
                    "postcode": "LS1 1AB",
                    "country": "United Kingdom",
                    "is_default": False,
                }
            ],
            headers=admin_headers,
        )
        second_id = r2.json()["data"][0]["id"]

        await client.patch(
            f"{ORGS}/{sample_org.id}/pickup-addresses/{second_id}",
            json={"is_default": True},
            headers=admin_headers,
        )

        resp = await client.get(f"{ORGS}/{sample_org.id}/pickup-addresses", headers=admin_headers)
        addresses = resp.json()["data"]
        defaults = [a for a in addresses if a["is_default"]]
        assert len(defaults) == 1
        assert defaults[0]["id"] == second_id

    @pytest.mark.asyncio
    async def test_delete_pickup_address(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """DELETE removes the address."""
        create_resp = await client.post(
            f"{ORGS}/{sample_org.id}/pickup-addresses",
            json=[
                {
                    "line_1": "To Delete",
                    "city": "London",
                    "state": "Greater London",
                    "postcode": "W1A 1AA",
                    "country": "United Kingdom",
                    "is_default": True,
                }
            ],
            headers=admin_headers,
        )
        address_id = create_resp.json()["data"][0]["id"]

        del_resp = await client.delete(f"{ORGS}/{sample_org.id}/pickup-addresses/{address_id}", headers=admin_headers)
        assert del_resp.status_code == 200

        list_resp = await client.get(f"{ORGS}/{sample_org.id}/pickup-addresses", headers=admin_headers)
        assert all(a["id"] != address_id for a in list_resp.json()["data"])

    @pytest.mark.asyncio
    async def test_delete_default_promotes_next(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """Deleting the default address auto-promotes the oldest remaining address."""
        r1 = await client.post(
            f"{ORGS}/{sample_org.id}/pickup-addresses",
            json=[
                {
                    "line_1": "Default",
                    "city": "London",
                    "state": "Greater London",
                    "postcode": "W1A 1AA",
                    "country": "United Kingdom",
                    "is_default": True,
                }
            ],
            headers=admin_headers,
        )
        await client.post(
            f"{ORGS}/{sample_org.id}/pickup-addresses",
            json=[
                {
                    "line_1": "Other",
                    "city": "Leeds",
                    "state": "Yorkshire",
                    "postcode": "LS1 1AB",
                    "country": "United Kingdom",
                    "is_default": False,
                }
            ],
            headers=admin_headers,
        )
        default_id = r1.json()["data"][0]["id"]

        await client.delete(f"{ORGS}/{sample_org.id}/pickup-addresses/{default_id}", headers=admin_headers)

        resp = await client.get(f"{ORGS}/{sample_org.id}/pickup-addresses", headers=admin_headers)
        addresses = resp.json()["data"]
        assert len(addresses) == 1
        assert addresses[0]["is_default"] is True
        assert addresses[0]["line_1"] == "Other"

    @pytest.mark.asyncio
    async def test_pickup_address_wrong_org_returns_404(self, client: AsyncClient, admin_headers: dict, sample_org: Organization, org_factory) -> None:
        """Address belonging to a different org raises 404."""
        other_org = await org_factory(reference="SWC-ORG-99901")
        create_resp = await client.post(
            f"{ORGS}/{other_org.id}/pickup-addresses",
            json=[
                {
                    "line_1": "Other Org",
                    "city": "London",
                    "state": "Greater London",
                    "postcode": "W1A 1AA",
                    "country": "United Kingdom",
                    "is_default": True,
                }
            ],
            headers=admin_headers,
        )
        address_id = create_resp.json()["data"][0]["id"]

        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/pickup-addresses/{address_id}",
            json={"city": "Bristol"},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_pickup_address_requires_admin(self, client: AsyncClient, auth_headers: dict, sample_org: Organization) -> None:
        """Non-admin gets 403 on pickup address create."""
        resp = await client.post(
            f"{ORGS}/{sample_org.id}/pickup-addresses",
            json=[
                {
                    "line_1": "Test",
                    "city": "London",
                    "state": "Greater London",
                    "postcode": "W1A 1AA",
                    "country": "United Kingdom",
                    "is_default": True,
                }
            ],
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_list_pickup_addresses_unknown_org_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        """Unknown org ID → 404."""
        resp = await client.get(f"{ORGS}/00000000-0000-0000-0000-000000000000/pickup-addresses", headers=admin_headers)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════
#  LOGO UPLOAD
# ═══════════════════════════════════════════════════


def _mock_upload_image(image_id: str = "cf-image-id-abc123"):
    """Patch Cloudflare Images upload so tests don't hit the network."""
    fake_result = MagicMock()
    fake_result.id = image_id
    return patch(
        "app.modules.organizations.service.upload_image",
        new_callable=AsyncMock,
        return_value=fake_result,
    )


def _mock_delete_image():
    """Patch Cloudflare Images delete."""
    return patch(
        "app.modules.organizations.service.delete_image",
        new_callable=AsyncMock,
        return_value=None,
    )


def _mock_generate_image_url(url: str = "https://imagedelivery.net/test/cf-image-id-abc123/public"):
    """Patch generate_image_url."""
    return patch(
        "app.modules.organizations.service.generate_image_url",
        return_value=url,
    )


class TestOrganizationLogo:
    """PATCH /v1/organizations/{id}/logo — logo upload."""

    @pytest.mark.asyncio
    async def test_upload_logo_success(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """Admin can upload a JPEG logo; response includes logo_url."""
        fake_image = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # minimal JPEG magic bytes
        with _mock_upload_image(), _mock_generate_image_url():
            resp = await client.patch(
                f"{ORGS}/{sample_org.id}/logo",
                files={"logo": ("logo.jpg", fake_image, "image/jpeg")},
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["id"] == sample_org.id
        assert data["logo_url"] is not None

    @pytest.mark.asyncio
    async def test_upload_logo_png_success(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """Admin can upload a PNG logo."""
        # Minimal valid PNG: signature + IHDR chunk (13 bytes) + IDAT + IEND
        import struct, zlib  # noqa: PLC0415, E401

        def _png_chunk(name: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(name + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)

        png_sig = b"\x89PNG\r\n\x1a\n"
        ihdr = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        idat_data = zlib.compress(b"\x00\xff\xff\xff")
        idat = _png_chunk(b"IDAT", idat_data)
        iend = _png_chunk(b"IEND", b"")
        fake_png = png_sig + ihdr + idat + iend

        with _mock_upload_image(), _mock_generate_image_url():
            resp = await client.patch(
                f"{ORGS}/{sample_org.id}/logo",
                files={"logo": ("logo.png", fake_png, "image/png")},
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_upload_logo_unknown_org_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        """Unknown org → 404 before upload."""
        fake_image = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        with _mock_upload_image(), _mock_generate_image_url():
            resp = await client.patch(
                f"{ORGS}/00000000-0000-0000-0000-000000000000/logo",
                files={"logo": ("logo.jpg", fake_image, "image/jpeg")},
                headers=admin_headers,
            )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_upload_logo_requires_admin(self, client: AsyncClient, auth_headers: dict, sample_org: Organization) -> None:
        """Non-admin gets 403."""
        fake_image = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/logo",
            files={"logo": ("logo.jpg", fake_image, "image/jpeg")},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_upload_logo_replaces_old_and_deletes_from_cf(
        self, client: AsyncClient, admin_headers: dict, db_session, sample_org: Organization
    ) -> None:
        """Uploading a second logo deletes the old CF image (best-effort)."""
        # Pre-set an existing logo ID directly on the org row
        from sqlalchemy import update as sa_update  # noqa: PLC0415

        from app.modules.organizations.models import Organization as OrgModel  # noqa: PLC0415

        await db_session.execute(
            sa_update(OrgModel).where(OrgModel.id == sample_org.id).values(logo_cf_image_id="old-cf-id-xyz")
        )
        await db_session.flush()

        fake_image = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        with _mock_upload_image("new-cf-id-abc"), _mock_delete_image() as mock_del, _mock_generate_image_url():
            resp = await client.patch(
                f"{ORGS}/{sample_org.id}/logo",
                files={"logo": ("logo.jpg", fake_image, "image/jpeg")},
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        mock_del.assert_awaited_once_with("old-cf-id-xyz")


# ═══════════════════════════════════════════════════
#  ACCOUNT MANAGER — per-org
# ═══════════════════════════════════════════════════


class TestOrgAccountManager:
    """GET/PATCH /v1/organizations/{id}/account-manager."""

    @pytest.mark.asyncio
    async def test_get_returns_null_when_unassigned(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """org with no account manager returns account_manager=null."""
        resp = await client.get(f"{ORGS}/{sample_org.id}/account-manager", headers=admin_headers)

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["org_id"] == sample_org.id
        assert data["account_manager"] is None

    @pytest.mark.asyncio
    async def test_assign_account_manager(self, client: AsyncClient, admin_headers: dict, sample_org: Organization, user_factory) -> None:
        """Admin can assign another admin user as account manager."""
        manager = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True, first_name="Alice", last_name="Manager")

        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/account-manager",
            json={"account_manager_user_id": manager.id},
            headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["org_id"] == sample_org.id
        assert data["account_manager"]["id"] == manager.id
        assert data["account_manager"]["first_name"] == "Alice"
        assert data["account_manager"]["role"] == "ADMIN"

    @pytest.mark.asyncio
    async def test_get_returns_assigned_manager(self, client: AsyncClient, admin_headers: dict, sample_org: Organization, user_factory) -> None:
        """GET reflects the currently assigned manager."""
        manager = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True, first_name="Bob", last_name="Admin")

        await client.patch(
            f"{ORGS}/{sample_org.id}/account-manager",
            json={"account_manager_user_id": manager.id},
            headers=admin_headers,
        )

        resp = await client.get(f"{ORGS}/{sample_org.id}/account-manager", headers=admin_headers)

        assert resp.status_code == 200
        assert resp.json()["data"]["account_manager"]["id"] == manager.id

    @pytest.mark.asyncio
    async def test_reassign_account_manager(self, client: AsyncClient, admin_headers: dict, sample_org: Organization, user_factory) -> None:
        """Assigning a new manager replaces the old one."""
        manager1 = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True, first_name="Old", last_name="Manager")
        manager2 = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True, first_name="New", last_name="Manager")

        await client.patch(
            f"{ORGS}/{sample_org.id}/account-manager",
            json={"account_manager_user_id": manager1.id},
            headers=admin_headers,
        )
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/account-manager",
            json={"account_manager_user_id": manager2.id},
            headers=admin_headers,
        )

        assert resp.status_code == 200
        assert resp.json()["data"]["account_manager"]["id"] == manager2.id

    @pytest.mark.asyncio
    async def test_unassign_account_manager(self, client: AsyncClient, admin_headers: dict, sample_org: Organization, user_factory) -> None:
        """Pass null to unassign the current manager."""
        manager = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        await client.patch(
            f"{ORGS}/{sample_org.id}/account-manager",
            json={"account_manager_user_id": manager.id},
            headers=admin_headers,
        )

        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/account-manager",
            json={"account_manager_user_id": None},
            headers=admin_headers,
        )

        assert resp.status_code == 200
        assert resp.json()["data"]["account_manager"] is None

    @pytest.mark.asyncio
    async def test_assign_non_admin_user_returns_422(self, client: AsyncClient, admin_headers: dict, sample_org: Organization, verified_user) -> None:
        """Assigning a non-admin user (e.g. CUSTOMER_B2C) → 422."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/account-manager",
            json={"account_manager_user_id": verified_user.id},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_assign_nonexistent_user_returns_404(self, client: AsyncClient, admin_headers: dict, sample_org: Organization) -> None:
        """Assigning a user ID that doesn't exist → 404."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/account-manager",
            json={"account_manager_user_id": "00000000-0000-0000-0000-000000000000"},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_account_manager_unknown_org_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        """Unknown org → 404."""
        resp = await client.get(f"{ORGS}/00000000-0000-0000-0000-000000000000/account-manager", headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_assign_requires_admin(self, client: AsyncClient, auth_headers: dict, sample_org: Organization, user_factory) -> None:
        """Non-admin gets 403 on assign."""
        manager = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/account-manager",
            json={"account_manager_user_id": manager.id},
            headers=auth_headers,
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  ACCOUNT MANAGERS LIST
# ═══════════════════════════════════════════════════


class TestListAccountManagers:
    """GET /v1/organizations/account-managers — list eligible account managers."""

    @pytest.mark.asyncio
    async def test_list_returns_paginated_response(self, client: AsyncClient, admin_headers: dict, user_factory) -> None:
        """Returns a paginated envelope with admin users only."""
        await user_factory(role="ADMIN", status="ACTIVE", email_verified=True, first_name="Zara", last_name="Smith")
        await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True, first_name="Yusuf", last_name="Khan")

        resp = await client.get(f"{ORGS}/account-managers", headers=admin_headers)

        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert "items" in body
        assert "total" in body
        assert "page" in body
        assert body["total"] >= 2

    @pytest.mark.asyncio
    async def test_list_only_returns_admin_roles(self, client: AsyncClient, admin_headers: dict, user_factory) -> None:
        """CUSTOMER_B2C users must not appear in the list."""
        await user_factory(role="ADMIN", status="ACTIVE", email_verified=True, first_name="AdminOnly", last_name="User")
        customer = await user_factory(role="CUSTOMER_B2C", status="ACTIVE", email_verified=True, first_name="Customer", last_name="User")

        resp = await client.get(f"{ORGS}/account-managers", headers=admin_headers)

        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        ids = [i["id"] for i in items]
        assert customer.id not in ids
        assert all(i["role"] in ("ADMIN", "SUPER_ADMIN") for i in items)

    @pytest.mark.asyncio
    async def test_list_search_by_name(self, client: AsyncClient, admin_headers: dict, user_factory) -> None:
        """Search filters by first or last name."""
        await user_factory(role="ADMIN", status="ACTIVE", email_verified=True, first_name="Unique", last_name="AccountMgr")

        resp = await client.get(f"{ORGS}/account-managers", params={"search": "AccountMgr"}, headers=admin_headers)

        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert any(i["last_name"] == "AccountMgr" for i in items)

    @pytest.mark.asyncio
    async def test_list_search_by_email(self, client: AsyncClient, admin_headers: dict, user_factory) -> None:
        """Search filters by email."""
        unique_email = f"searchable-admin-{uuid.uuid4().hex[:8]}@example.com"
        await user_factory(role="ADMIN", status="ACTIVE", email_verified=True, email=unique_email)

        resp = await client.get(f"{ORGS}/account-managers", params={"search": unique_email[:20]}, headers=admin_headers)

        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert any(i["email"] == unique_email for i in items)

    @pytest.mark.asyncio
    async def test_list_pagination(self, client: AsyncClient, admin_headers: dict, user_factory) -> None:
        """size param limits page results."""
        for i in range(3):
            await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

        resp = await client.get(f"{ORGS}/account-managers", params={"page": 1, "size": 2}, headers=admin_headers)

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert len(body["items"]) <= 2
        assert body["size"] == 2

    @pytest.mark.asyncio
    async def test_list_items_contain_expected_fields(self, client: AsyncClient, admin_headers: dict, user_factory) -> None:
        """Each item has id, first_name, last_name, full_name, email, role."""
        await user_factory(role="ADMIN", status="ACTIVE", email_verified=True, first_name="Field", last_name="Check")

        resp = await client.get(f"{ORGS}/account-managers", headers=admin_headers)

        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) >= 1
        item = items[0]
        for field in ("id", "first_name", "last_name", "full_name", "email", "role"):
            assert field in item, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_list_requires_admin(self, client: AsyncClient, auth_headers: dict) -> None:
        """Non-admin gets 403."""
        resp = await client.get(f"{ORGS}/account-managers", headers=auth_headers)
        assert resp.status_code == 403


class TestContractMetadataFields:
    """GET/PATCH /v1/organizations/{id} — contract_title, contract_expiry_date, contract_url fields."""

    @pytest.mark.asyncio
    async def test_get_org_contract_fields_present(
        self, client: AsyncClient, admin_headers: dict, sample_org: Organization
    ) -> None:
        """GET response always contains contract_reference, contract_title,
        contract_expiry_date, and contract_url keys (null when no contract uploaded)."""
        resp = await client.get(f"{ORGS}/{sample_org.id}", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        for field in ("contract_reference", "contract_title", "contract_expiry_date", "contract_url"):
            assert field in data, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_patch_org_returns_contract_url(
        self, client: AsyncClient, admin_headers: dict, sample_org: Organization, db_session: AsyncSession
    ) -> None:
        """PATCH response must also include contract_url (not null when a contract_reference exists)."""
        from datetime import date
        from app.modules.organizations.repository import OrganizationRepository
        repo = OrganizationRepository(db_session)
        await repo.update_by_id(
            sample_org.id,
            {
                "contract_reference": f"organizations/{sample_org.id}/contracts/test_abc12345.pdf",
                "contract_title": "Test Agreement",
                "contract_expiry_date": date(2027, 12, 31),
            },
        )

        with patch("app.modules.organizations.service.generate_document_url", return_value="https://example.com/presigned"):
            resp = await client.patch(
                f"{ORGS}/{sample_org.id}",
                json={"notes": "updated", "reason": "test update"},
                headers=admin_headers,
            )

        assert resp.status_code == 200
        org = resp.json()["data"]["organization"]
        assert org["contract_title"] == "Test Agreement"
        assert org["contract_expiry_date"] == "2027-12-31"
        assert org["contract_url"] == "https://example.com/presigned"

    @pytest.mark.asyncio
    async def test_r2_key_not_in_org_response(
        self, client: AsyncClient, admin_headers: dict, sample_org: Organization
    ) -> None:
        """r2_key must never appear in the org GET response."""
        resp = await client.get(f"{ORGS}/{sample_org.id}", headers=admin_headers)
        assert resp.status_code == 200
        assert "r2_key" not in resp.json()["data"]


class TestListOrganizationsDateFilters:
    """GET /v1/organizations — created_to inclusive end-of-day on month boundaries."""

    @pytest.mark.asyncio
    async def test_list_created_to_month_end_does_not_500(
        self, client: AsyncClient, admin_headers: dict
    ) -> None:
        resp = await client.get(
            ORGS,
            params={"created_to": "2026-01-31"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert "data" in resp.json()
