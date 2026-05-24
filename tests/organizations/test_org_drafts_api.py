"""Integration API tests — Organisation Draft endpoints.

Covers:
- POST   /v1/organizations/drafts              create draft (all fields optional)
- GET    /v1/organizations/drafts              list drafts (paginated + search)
- GET    /v1/organizations/drafts/{draft_number}   single draft
- PATCH  /v1/organizations/drafts/{draft_number}   partial update
- DELETE /v1/organizations/drafts/{draft_number}   hard delete
- POST   /v1/organizations/drafts/{draft_number}/publish   DRAFT → ACTIVE

All tests use per-test transaction rollback (no persistent state).
Arq/invite machinery is mocked so no Redis/SMTP is needed.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user.models import User

DRAFTS = "/v1/organizations/drafts"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _draft_form(**overrides) -> dict:
    """Build form data for POST/PATCH /drafts. All fields optional."""
    data: dict = {}
    data.update(overrides)
    return data


def _full_draft_form(**overrides) -> dict:
    """Build a fully-filled draft form (publishable without extra PATCH)."""
    suffix = uuid.uuid4().hex[:8]
    data = {
        "trading_name": f"Full Draft Corp {suffix}",
        "legal_entity_name": f"Full Draft Corp {suffix} Limited",
        "industry": "LOGISTICS_TRANSPORT",
        "company_size": "11-50 employees",
        "companies_house_number": suffix[:8],
        "date_of_incorporation": "2019-06-15",
        "reg_address_line_1": "10 Publish Lane",
        "reg_city": "Manchester",
        "reg_postcode": "M1 1AA",
    }
    data.update(overrides)
    return data


def _contact_json(**overrides) -> str:
    """JSON-encoded contacts list with one ACCOUNT_OWNER."""
    suffix = uuid.uuid4().hex[:8]
    contact = {
        "email": f"owner-{suffix}@drafts.example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "contact_number": "+447700900000",
        "contact_role": "ACCOUNT_OWNER",
    }
    contact.update(overrides)
    return json.dumps([contact])


def _mock_enqueue():
    return patch("app.modules.organizations.service.enqueue", new_callable=AsyncMock, return_value=None)


def _mock_create_invite():
    from app.modules.auth.service import CreateInviteResult

    fake_invite = MagicMock()
    fake_invite.id = "invite-draft-fake"
    fake_user = MagicMock()
    return patch(
        "app.modules.organizations.service.AuthService.create_invite",
        new_callable=AsyncMock,
        return_value=CreateInviteResult(False, fake_invite, "raw-token-draft", fake_user, "invite-draft-fake"),
    )


# ═══════════════════════════════════════════════════
#  CREATE
# ═══════════════════════════════════════════════════


class TestCreateDraft:
    """POST /v1/organizations/drafts"""

    @pytest.mark.asyncio
    async def test_create_empty_draft_returns_201(self, client: AsyncClient, admin_headers: dict) -> None:
        """Creating a draft with no fields returns 201 with status=DRAFT and a draft_number."""
        resp = await client.post(DRAFTS, data=_draft_form(), headers=admin_headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["status"] == "DRAFT"
        assert data["draft_number"].startswith("ORG-D-")
        assert data["reference"].startswith("SWC-ORG-")

    @pytest.mark.asyncio
    async def test_create_with_trading_name(self, client: AsyncClient, admin_headers: dict) -> None:
        resp = await client.post(
            DRAFTS,
            data=_draft_form(trading_name="Acme Draft Ltd"),
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["trading_name"] == "Acme Draft Ltd"

    @pytest.mark.asyncio
    async def test_create_with_contacts_stores_in_jsonb(self, client: AsyncClient, admin_headers: dict) -> None:
        """Contacts provided at create time are stored in draft_contacts JSONB."""
        resp = await client.post(
            DRAFTS,
            data=_draft_form(contacts=_contact_json()),
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert isinstance(data["draft_contacts"], list)
        assert len(data["draft_contacts"]) == 1
        assert data["draft_contacts"][0]["contact_role"] == "ACCOUNT_OWNER"

    @pytest.mark.asyncio
    async def test_create_sets_draft_created_by(self, client: AsyncClient, admin_headers: dict, admin_user: User) -> None:
        resp = await client.post(DRAFTS, data=_draft_form(), headers=admin_headers)
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["draft_created_by_id"] == admin_user.id

    @pytest.mark.asyncio
    async def test_create_requires_admin(self, client: AsyncClient, auth_headers: dict) -> None:
        """Non-admin users cannot create drafts."""
        resp = await client.post(DRAFTS, data=_draft_form(), headers=auth_headers)
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_create_unauthenticated_returns_401(self, client: AsyncClient) -> None:
        resp = await client.post(DRAFTS, data=_draft_form(), headers={"X-Client-Type": "ADMIN"})
        assert resp.status_code == 401, resp.text


# ═══════════════════════════════════════════════════
#  READ
# ═══════════════════════════════════════════════════


class TestGetDraft:
    """GET /v1/organizations/drafts/{draft_number}"""

    @pytest.mark.asyncio
    async def test_get_existing_draft(self, client: AsyncClient, admin_headers: dict) -> None:
        create = await client.post(DRAFTS, data=_draft_form(trading_name="Get Me"), headers=admin_headers)
        assert create.status_code == 201
        draft_number = create.json()["data"]["draft_number"]

        resp = await client.get(f"{DRAFTS}/{draft_number}", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["draft_number"] == draft_number
        assert data["trading_name"] == "Get Me"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        resp = await client.get(f"{DRAFTS}/ORG-D-99999", headers=admin_headers)
        assert resp.status_code == 404, resp.text


class TestListDrafts:
    """GET /v1/organizations/drafts"""

    @pytest.mark.asyncio
    async def test_list_returns_paginated_response(self, client: AsyncClient, admin_headers: dict) -> None:
        resp = await client.get(DRAFTS, headers=admin_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "items" in data
        assert "total" in data
        assert "page" in data

    @pytest.mark.asyncio
    async def test_list_shows_created_draft(self, client: AsyncClient, admin_headers: dict) -> None:
        suffix = uuid.uuid4().hex[:6]
        trading_name = f"Listable Draft {suffix}"
        create = await client.post(DRAFTS, data=_draft_form(trading_name=trading_name), headers=admin_headers)
        assert create.status_code == 201

        resp = await client.get(DRAFTS, params={"search": suffix}, headers=admin_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert any(it.get("trading_name") == trading_name for it in items)

    @pytest.mark.asyncio
    async def test_list_excludes_published_orgs(self, client: AsyncClient, admin_headers: dict) -> None:
        """After publishing, the draft disappears from the draft list."""
        create = await client.post(DRAFTS, data=_full_draft_form(), headers=admin_headers)
        assert create.status_code == 201
        draft_number = create.json()["data"]["draft_number"]

        publish_body = json.dumps({"contacts": json.loads(_contact_json())})
        with _mock_enqueue(), _mock_create_invite():
            pub = await client.post(
                f"{DRAFTS}/{draft_number}/publish",
                data={"body": publish_body},
                headers=admin_headers,
            )
        assert pub.status_code == 201

        resp = await client.get(DRAFTS, params={"search": draft_number}, headers=admin_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert not any(it.get("draft_number") == draft_number for it in items)

    @pytest.mark.asyncio
    async def test_list_requires_admin(self, client: AsyncClient, auth_headers: dict) -> None:
        resp = await client.get(DRAFTS, headers=auth_headers)
        assert resp.status_code == 403, resp.text


# ═══════════════════════════════════════════════════
#  UPDATE
# ═══════════════════════════════════════════════════


class TestUpdateDraft:
    """PATCH /v1/organizations/drafts/{draft_number}"""

    @pytest.mark.asyncio
    async def test_patch_updates_fields(self, client: AsyncClient, admin_headers: dict) -> None:
        create = await client.post(DRAFTS, data=_draft_form(), headers=admin_headers)
        assert create.status_code == 201
        draft_number = create.json()["data"]["draft_number"]

        resp = await client.patch(
            f"{DRAFTS}/{draft_number}",
            data=_draft_form(
                trading_name="Patched Name",
                legal_entity_name="Patched Legal Name Limited",
                industry="LOGISTICS_TRANSPORT",
            ),
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["trading_name"] == "Patched Name"
        assert data["legal_entity_name"] == "Patched Legal Name Limited"
        assert data["industry"] == "LOGISTICS_TRANSPORT"

    @pytest.mark.asyncio
    async def test_patch_replaces_contacts(self, client: AsyncClient, admin_headers: dict) -> None:
        create = await client.post(
            DRAFTS,
            data=_draft_form(contacts=_contact_json(email="original@drafts.example.com")),
            headers=admin_headers,
        )
        assert create.status_code == 201
        draft_number = create.json()["data"]["draft_number"]

        new_email = f"new-{uuid.uuid4().hex[:6]}@drafts.example.com"
        resp = await client.patch(
            f"{DRAFTS}/{draft_number}",
            data=_draft_form(contacts=_contact_json(email=new_email)),
            headers=admin_headers,
        )
        assert resp.status_code == 200
        contacts = resp.json()["data"]["draft_contacts"]
        assert contacts[0]["email"] == new_email

    @pytest.mark.asyncio
    async def test_patch_nonexistent_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        resp = await client.patch(f"{DRAFTS}/ORG-D-99999", data=_draft_form(trading_name="XY"), headers=admin_headers)
        assert resp.status_code == 404, resp.text

    @pytest.mark.asyncio
    async def test_patch_requires_admin(self, client: AsyncClient, auth_headers: dict) -> None:
        resp = await client.patch(f"{DRAFTS}/ORG-D-00001", data=_draft_form(), headers=auth_headers)
        assert resp.status_code == 403, resp.text


# ═══════════════════════════════════════════════════
#  DELETE
# ═══════════════════════════════════════════════════


class TestDeleteDraft:
    """DELETE /v1/organizations/drafts/{draft_number}"""

    @pytest.mark.asyncio
    async def test_delete_removes_draft(self, client: AsyncClient, admin_headers: dict) -> None:
        create = await client.post(DRAFTS, data=_draft_form(trading_name="Delete Me"), headers=admin_headers)
        assert create.status_code == 201
        draft_number = create.json()["data"]["draft_number"]

        del_resp = await client.delete(f"{DRAFTS}/{draft_number}", headers=admin_headers)
        assert del_resp.status_code == 200, del_resp.text
        assert del_resp.json()["success"] is True

        get_resp = await client.get(f"{DRAFTS}/{draft_number}", headers=admin_headers)
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        resp = await client.delete(f"{DRAFTS}/ORG-D-99999", headers=admin_headers)
        assert resp.status_code == 404, resp.text

    @pytest.mark.asyncio
    async def test_delete_requires_admin(self, client: AsyncClient, auth_headers: dict) -> None:
        resp = await client.delete(f"{DRAFTS}/ORG-D-00001", headers=auth_headers)
        assert resp.status_code == 403, resp.text


# ═══════════════════════════════════════════════════
#  PUBLISH
# ═══════════════════════════════════════════════════


class TestPublishDraft:
    """POST /v1/organizations/drafts/{draft_number}/publish"""

    @pytest.mark.asyncio
    async def test_publish_invalid_json_body_returns_validation_error(
        self, client: AsyncClient, admin_headers: dict
    ) -> None:
        create = await client.post(DRAFTS, data=_full_draft_form(), headers=admin_headers)
        assert create.status_code == 201
        draft_number = create.json()["data"]["draft_number"]

        resp = await client.post(
            f"{DRAFTS}/{draft_number}/publish",
            data={"body": "not-json"},
            headers=admin_headers,
        )
        assert resp.status_code in (400, 422)
        message = str(resp.json()).lower()
        assert "traceback" not in message
        assert "not-json" not in message

    @pytest.mark.asyncio
    async def test_publish_transitions_to_active(self, client: AsyncClient, admin_headers: dict) -> None:
        """A fully-filled draft with a contact publishes to ACTIVE."""
        create = await client.post(DRAFTS, data=_full_draft_form(), headers=admin_headers)
        assert create.status_code == 201
        draft_number = create.json()["data"]["draft_number"]

        publish_body = json.dumps({"contacts": json.loads(_contact_json())})
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(
                f"{DRAFTS}/{draft_number}/publish",
                data={"body": publish_body},
                headers=admin_headers,
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["organization"]["status"] == "ACTIVE"
        assert len(data["contacts"]) == 1
        assert data["contacts"][0]["invite_token"] == "raw-token-draft"

    @pytest.mark.asyncio
    async def test_publish_syncs_pricing_contract_lines(
        self,
        client: AsyncClient,
        admin_headers: dict,
        pricing_tier_ids: list[str],
        db_session: AsyncSession,
    ) -> None:
        """Publishing draft with pricing_plans creates org_service_tier_contract_lines."""
        plans = [
            {
                "plain_name": "Draft Standard",
                "price_per_package": "9.99",
                "plain_type": "standard",
                "selected": True,
                "permitted": True,
                "days": 2,
                "id_price_tier": pricing_tier_ids[0],
            }
        ]
        create = await client.post(
            DRAFTS,
            data=_full_draft_form(pricing_plans=json.dumps(plans)),
            headers=admin_headers,
        )
        assert create.status_code == 201
        draft_number = create.json()["data"]["draft_number"]

        publish_body = json.dumps({"contacts": json.loads(_contact_json())})
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(
                f"{DRAFTS}/{draft_number}/publish",
                data={"body": publish_body},
                headers=admin_headers,
            )
        assert resp.status_code == 201, resp.text
        org_id = resp.json()["data"]["organization"]["id"]

        result = await db_session.execute(
            text("SELECT COUNT(*) FROM org_service_tier_contract_lines WHERE organization_id = :org_id"),
            {"org_id": org_id},
        )
        assert result.scalar_one() == 1

    @pytest.mark.asyncio
    async def test_publish_uses_saved_contacts_when_none_provided(self, client: AsyncClient, admin_headers: dict) -> None:
        """If contacts were saved in draft_contacts JSONB, publish uses those."""
        create = await client.post(
            DRAFTS,
            data=_full_draft_form(contacts=_contact_json()),
            headers=admin_headers,
        )
        assert create.status_code == 201
        draft_number = create.json()["data"]["draft_number"]

        # Publish with no contacts in body — should fall back to draft_contacts
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(
                f"{DRAFTS}/{draft_number}/publish",
                data={"body": json.dumps({})},
                headers=admin_headers,
            )
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["organization"]["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_publish_fails_missing_required_fields(self, client: AsyncClient, admin_headers: dict) -> None:
        """Publishing a draft that's missing required org fields returns 422."""
        create = await client.post(DRAFTS, data=_draft_form(trading_name="Incomplete"), headers=admin_headers)
        assert create.status_code == 201
        draft_number = create.json()["data"]["draft_number"]

        publish_body = json.dumps({"contacts": json.loads(_contact_json())})
        resp = await client.post(
            f"{DRAFTS}/{draft_number}/publish",
            data={"body": publish_body},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        assert "missing required fields" in resp.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_publish_fails_no_contacts(self, client: AsyncClient, admin_headers: dict) -> None:
        """Publishing without any contacts (not in body, not in JSONB) returns 422."""
        create = await client.post(DRAFTS, data=_full_draft_form(), headers=admin_headers)
        assert create.status_code == 201
        draft_number = create.json()["data"]["draft_number"]

        resp = await client.post(
            f"{DRAFTS}/{draft_number}/publish",
            data={"body": json.dumps({})},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_publish_fails_no_account_owner_contact(self, client: AsyncClient, admin_headers: dict) -> None:
        """Contacts without an ACCOUNT_OWNER role are rejected at publish."""
        create = await client.post(DRAFTS, data=_full_draft_form(), headers=admin_headers)
        assert create.status_code == 201
        draft_number = create.json()["data"]["draft_number"]

        contacts_no_owner = json.dumps([{
            "email": f"nobody-{uuid.uuid4().hex[:6]}@test.com",
            "first_name": "No",
            "last_name": "Owner",
            "contact_number": "+447700900000",
            "contact_role": "BILLING",
        }])
        publish_body = json.dumps({"contacts": json.loads(contacts_no_owner)})
        resp = await client.post(
            f"{DRAFTS}/{draft_number}/publish",
            data={"body": publish_body},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        assert "ACCOUNT_OWNER" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_publish_with_pickup_addresses(self, client: AsyncClient, admin_headers: dict) -> None:
        """Optional pickup_addresses are persisted via pickup_addresses (same as full org create)."""
        create = await client.post(DRAFTS, data=_full_draft_form(), headers=admin_headers)
        assert create.status_code == 201
        draft_number = create.json()["data"]["draft_number"]

        pickup = [
            {
                "line_1": "99 Draft Warehouse",
                "city": "Bristol",
                "state": "England",
                "postcode": "BS1 5TR",
                "country": "United Kingdom",
                "is_default": True,
            }
        ]
        publish_body = json.dumps({
            "contacts": json.loads(_contact_json()),
            "pickup_addresses": pickup,
        })
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(
                f"{DRAFTS}/{draft_number}/publish",
                data={"body": publish_body},
                headers=admin_headers,
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["pickup_addresses"] is not None
        assert len(data["pickup_addresses"]) == 1
        pa = data["pickup_addresses"][0]
        assert pa["line_1"] == "99 Draft Warehouse"
        assert pa["city"] == "Bristol"
        assert pa["is_default"] is True

    @pytest.mark.asyncio
    async def test_publish_nonexistent_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        publish_body = json.dumps({"contacts": json.loads(_contact_json())})
        resp = await client.post(
            f"{DRAFTS}/ORG-D-99999/publish",
            data={"body": publish_body},
            headers=admin_headers,
        )
        assert resp.status_code == 404, resp.text

    @pytest.mark.asyncio
    async def test_publish_already_active_returns_422(self, client: AsyncClient, admin_headers: dict) -> None:
        """Re-publishing an already-ACTIVE org returns 422."""
        create = await client.post(DRAFTS, data=_full_draft_form(), headers=admin_headers)
        assert create.status_code == 201
        draft_number = create.json()["data"]["draft_number"]

        publish_body = json.dumps({"contacts": json.loads(_contact_json())})
        with _mock_enqueue(), _mock_create_invite():
            first = await client.post(
                f"{DRAFTS}/{draft_number}/publish",
                data={"body": publish_body},
                headers=admin_headers,
            )
        assert first.status_code == 201

        second = await client.post(
            f"{DRAFTS}/{draft_number}/publish",
            data={"body": publish_body},
            headers=admin_headers,
        )
        assert second.status_code in (404, 422)

    @pytest.mark.asyncio
    async def test_publish_requires_admin(self, client: AsyncClient, auth_headers: dict) -> None:
        resp = await client.post(
            f"{DRAFTS}/ORG-D-00001/publish",
            data={"body": "{}"},
            headers=auth_headers,
        )
        assert resp.status_code == 403, resp.text
