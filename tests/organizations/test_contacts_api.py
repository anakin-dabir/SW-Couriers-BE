"""Integration API tests — OrgContact management endpoints.

Covers:
- GET    /v1/organizations/{id}/contacts              list contacts
- GET    /v1/organizations/{id}/contacts/{cid}        read single contact
- POST   /v1/organizations/{id}/contacts              add contact
- PATCH  /v1/organizations/{id}/contacts/{cid}        update contact
- DELETE /v1/organizations/{id}/contacts/{cid}        remove contact (soft-delete)
- POST   /v1/organizations/{id}/contacts/{cid}/set-primary  set primary contact

All tests use per-test transaction rollback. Arq jobs are mocked.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.organizations.models import OrgContact, Organization
from app.modules.organizations.enums import ContactRole, ContactStatus
from app.modules.user.models import User

ORGS = "/v1/organizations"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _mock_enqueue():
    return patch("app.modules.organizations.service.enqueue", new_callable=AsyncMock, return_value=None)


def _mock_create_invite():
    from app.modules.auth.service import CreateInviteResult

    fake_invite = MagicMock()
    fake_invite.id = "fake-invite-id"
    fake_user = MagicMock()
    return patch(
        "app.modules.organizations.service.AuthService.create_invite",
        new_callable=AsyncMock,
        return_value=CreateInviteResult(False, fake_invite, "raw-token-xyz", fake_user, "fake-invite-id"),
    )


def _b2b_headers(user_id: str, org_id: str) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user_id,
        role="CUSTOMER_B2B",
        client_type="CUSTOMER_B2B",
        organization_id=org_id,
    )
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "CUSTOMER_B2B"}


def _contacts_from_list_response(payload: dict) -> list[dict]:
    """Normalize contacts list payload into a flat list."""
    owner = payload.get("owner")
    team_members = payload.get("team_members") or []
    items: list[dict] = []
    if owner is not None:
        items.append(owner)
    items.extend(team_members)
    return items


async def _make_contact(
    db_session: AsyncSession,
    user_factory,
    org: Organization,
    role: str = "ACCOUNT_OWNER",
    is_primary: bool = False,
    status: str = "ACTIVE",
) -> OrgContact:
    """Create a User + OrgContact row directly in the DB."""
    user = await user_factory(
        role="CUSTOMER_B2B",
        status="ACTIVE",
        email_verified=True,
        organization_id=org.id,
    )
    contact = OrgContact(
        organization_id=org.id,
        user_id=user.id,
        contact_number=f"+447700{uuid.uuid4().int % 1000000:06d}",
        contact_role=role,
        status=status,
        is_primary=is_primary,
    )
    db_session.add(contact)
    await db_session.flush()
    await db_session.refresh(contact)
    return contact


# ── List contacts ──────────────────────────────────────────────────────────────


class TestListOrgContacts:
    """GET /v1/organizations/{id}/contacts"""

    @pytest.mark.asyncio
    async def test_admin_can_list_contacts(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """Admin gets full list of active contacts with PII fields."""
        await _make_contact(db_session, user_factory, sample_org, role="ACCOUNT_OWNER", is_primary=True)
        await _make_contact(db_session, user_factory, sample_org, role="BILLING")

        resp = await client.get(f"{ORGS}/{sample_org.id}/contacts", headers=admin_headers)

        assert resp.status_code == 200
        contacts = _contacts_from_list_response(resp.json()["data"])
        assert isinstance(contacts, list)
        assert len(contacts) >= 2
        # Admin gets full PII (email, phone)
        for c in contacts:
            assert "first_name" in c
            assert "contact_role" in c

    @pytest.mark.asyncio
    async def test_list_excludes_inactive_contacts(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """INACTIVE contacts are excluded from the list."""
        await _make_contact(db_session, user_factory, sample_org, role="ACCOUNT_OWNER", is_primary=True)
        await _make_contact(db_session, user_factory, sample_org, status="INACTIVE")

        resp = await client.get(f"{ORGS}/{sample_org.id}/contacts", headers=admin_headers)

        assert resp.status_code == 200
        statuses = [c["status"] for c in _contacts_from_list_response(resp.json()["data"])]
        assert "INACTIVE" not in statuses

    @pytest.mark.asyncio
    async def test_list_not_found_org_returns_404(self, client: AsyncClient, admin_headers: dict) -> None:
        """Unknown org → 404."""
        resp = await client.get(
            f"{ORGS}/00000000-0000-0000-0000-000000000000/contacts",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_requires_auth(self, client: AsyncClient, sample_org: Organization) -> None:
        """No auth → 401."""
        resp = await client.get(
            f"{ORGS}/{sample_org.id}/contacts",
            headers={"X-Client-Type": "ADMIN"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_requires_member_or_admin(
        self,
        client: AsyncClient,
        auth_headers: dict,
        sample_org: Organization,
    ) -> None:
        """B2C user (non-member) gets 403."""
        resp = await client.get(f"{ORGS}/{sample_org.id}/contacts", headers=auth_headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_list_primary_contact_first(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """Primary contact (is_primary=True) is returned before non-primary contacts."""
        await _make_contact(db_session, user_factory, sample_org, is_primary=False)
        await _make_contact(db_session, user_factory, sample_org, is_primary=True)

        resp = await client.get(f"{ORGS}/{sample_org.id}/contacts", headers=admin_headers)

        assert resp.status_code == 200
        contacts = _contacts_from_list_response(resp.json()["data"])
        if len(contacts) >= 2:
            assert contacts[0]["is_primary"] is True


# ── Get single contact ─────────────────────────────────────────────────────────


class TestGetOrgContact:
    """GET /v1/organizations/{id}/contacts/{cid}"""

    @pytest.mark.asyncio
    async def test_admin_can_get_single_contact(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """Admin fetches a single contact with full details."""
        contact = await _make_contact(db_session, user_factory, sample_org)

        resp = await client.get(
            f"{ORGS}/{sample_org.id}/contacts/{contact.id}",
            headers=admin_headers,
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == contact.id
        assert data["organization_id"] == sample_org.id
        assert data["contact_role"] in ("ACCOUNT_OWNER", "BILLING", "OPERATIONS", "FINANCE")

    @pytest.mark.asyncio
    async def test_get_contact_not_found_returns_404(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org: Organization,
    ) -> None:
        """Unknown contact ID → 404."""
        resp = await client.get(
            f"{ORGS}/{sample_org.id}/contacts/00000000-0000-0000-0000-000000000000",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_contact_cross_org_returns_404(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        org_factory,
        user_factory,
    ) -> None:
        """Contact scoped to org-A cannot be fetched via org-B URL (cross-org safety)."""
        org_a = await org_factory(reference="SWC-ORG-09001")
        org_b = await org_factory(reference="SWC-ORG-09002")
        contact = await _make_contact(db_session, user_factory, org_a)

        resp = await client.get(
            f"{ORGS}/{org_b.id}/contacts/{contact.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 404


# ── Add contact ────────────────────────────────────────────────────────────────


class TestAddOrgContact:
    """POST /v1/organizations/{id}/contacts"""

    @pytest.mark.asyncio
    async def test_admin_can_add_contact(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org: Organization,
    ) -> None:
        """Admin adds a new contact; invite is enqueued."""
        body = {
            "email": f"newcontact-{uuid.uuid4().hex[:8]}@example.com",
            "first_name": "New",
            "last_name": "Contact",
            "contact_number": "+447700000099",
            "contact_role": "BILLING",
        }
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(
                f"{ORGS}/{sample_org.id}/contacts",
                json=body,
                headers=admin_headers,
            )

        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["contact_role"] == "BILLING"
        assert data["organization_id"] == sample_org.id

    @pytest.mark.asyncio
    async def test_add_contact_duplicate_email_returns_409(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """Adding a contact with an already-used email → 409."""
        existing_user = await user_factory(email="already@exists.com", role="CUSTOMER_B2B")
        body = {
            "email": "already@exists.com",
            "first_name": "Dup",
            "last_name": "Contact",
            "contact_number": "+447700000011",
            "contact_role": "ACCOUNT_OWNER",
        }
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(
                f"{ORGS}/{sample_org.id}/contacts",
                json=body,
                headers=admin_headers,
            )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_add_contact_missing_required_field_returns_422(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org: Organization,
    ) -> None:
        """Missing email → 422."""
        body = {
            "first_name": "Bad",
            "last_name": "Contact",
            "contact_number": "+447700000022",
            "contact_role": "ACCOUNT_OWNER",
        }
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(
                f"{ORGS}/{sample_org.id}/contacts",
                json=body,
                headers=admin_headers,
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_add_contact_to_nonexistent_org_returns_404(
        self,
        client: AsyncClient,
        admin_headers: dict,
    ) -> None:
        """Adding contact to unknown org → 404."""
        body = {
            "email": f"ghost-{uuid.uuid4().hex[:8]}@example.com",
            "first_name": "Ghost",
            "last_name": "Contact",
            "contact_number": "+447700000033",
            "contact_role": "ACCOUNT_OWNER",
        }
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(
                f"{ORGS}/00000000-0000-0000-0000-000000000000/contacts",
                json=body,
                headers=admin_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_add_contact_requires_admin_or_account_owner(
        self,
        client: AsyncClient,
        auth_headers: dict,
        sample_org: Organization,
    ) -> None:
        """B2C user → 403."""
        body = {
            "email": f"forbidden-{uuid.uuid4().hex[:8]}@example.com",
            "first_name": "F",
            "last_name": "O",
            "contact_number": "+447700000044",
            "contact_role": "BILLING",
        }
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(
                f"{ORGS}/{sample_org.id}/contacts",
                json=body,
                headers=auth_headers,
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_same_org_b2b_with_contacts_write_can_add_contact(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        member_contact = await _make_contact(db_session, user_factory, sample_org, role="BILLING", is_primary=False)
        grant = await client.put(
            f"/v1/permissions/{member_contact.user_id}",
            headers=admin_headers,
            json={"resource": "CONTACTS", "level": "WRITE"},
        )
        assert grant.status_code == 200, grant.text

        body = {
            "email": f"delegated-write-{uuid.uuid4().hex[:8]}@example.com",
            "first_name": "Delegated",
            "last_name": "Writer",
            "contact_number": "+447700000455",
            "contact_role": "OPERATIONS",
        }
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(
                f"{ORGS}/{sample_org.id}/contacts",
                json=body,
                headers=_b2b_headers(member_contact.user_id, sample_org.id),
            )
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["contact_role"] == "OPERATIONS"

    @pytest.mark.asyncio
    async def test_super_admin_can_add_contact(
        self,
        client: AsyncClient,
        user_factory,
        sample_org: Organization,
    ) -> None:
        """SUPER_ADMIN can manage contacts same as ADMIN."""
        super_admin = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)
        token, _ = create_access_token(
            user_id=super_admin.id,
            role=super_admin.role,
            client_type="ADMIN",
        )
        super_admin_headers = {
            "Authorization": f"Bearer {token}",
            "X-Client-Type": "ADMIN",
        }

        body = {
            "email": f"superadmin-contact-{uuid.uuid4().hex[:8]}@example.com",
            "first_name": "Super",
            "last_name": "Admin",
            "contact_number": "+447700001122",
            "contact_role": "BILLING",
        }
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(
                f"{ORGS}/{sample_org.id}/contacts",
                json=body,
                headers=super_admin_headers,
            )
        assert resp.status_code == 201, resp.text

    @pytest.mark.asyncio
    async def test_add_account_owner_auto_grants_audit_log_read(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org: Organization,
    ) -> None:
        """ACCOUNT_OWNER receives AUDIT_LOG READ even without explicit permissions payload."""
        body = {
            "email": f"owner-audit-{uuid.uuid4().hex[:8]}@example.com",
            "first_name": "Owner",
            "last_name": "Audit",
            "contact_number": "+447700001133",
            "contact_role": "ACCOUNT_OWNER",
        }
        with _mock_enqueue(), _mock_create_invite():
            create_resp = await client.post(
                f"{ORGS}/{sample_org.id}/contacts",
                json=body,
                headers=admin_headers,
            )
        assert create_resp.status_code == 201, create_resp.text
        user_id = create_resp.json()["data"]["user_id"]

        perm_resp = await client.get(f"/v1/permissions/{user_id}", headers=admin_headers)
        assert perm_resp.status_code == 200, perm_resp.text
        permissions = {
            p["resource"]: p["level"]
            for p in perm_resp.json()["data"]["permissions"]
        }
        assert permissions["AUDIT_LOG"] == "READ"


# ── Update contact ─────────────────────────────────────────────────────────────


class TestUpdateOrgContact:
    """PATCH /v1/organizations/{id}/contacts/{cid}"""

    @pytest.mark.asyncio
    async def test_admin_can_update_contact_role(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """Admin can change contact_role."""
        contact = await _make_contact(db_session, user_factory, sample_org, role="BILLING")

        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/contacts/{contact.id}",
            json={"contact_role": "OPERATIONS"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["contact_role"] == "OPERATIONS"

    @pytest.mark.asyncio
    async def test_admin_can_update_contact_number(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """Admin can update contact_number."""
        contact = await _make_contact(db_session, user_factory, sample_org)

        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/contacts/{contact.id}",
            json={"contact_number": "+441234567890"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["contact_number"] == "+441234567890"

    @pytest.mark.asyncio
    async def test_update_not_found_returns_404(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org: Organization,
    ) -> None:
        """Unknown contact ID → 404."""
        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/contacts/00000000-0000-0000-0000-000000000000",
            json={"contact_role": "BILLING"},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_requires_admin_or_account_owner(
        self,
        client: AsyncClient,
        auth_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """B2C user → 403."""
        contact = await _make_contact(db_session, user_factory, sample_org)

        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/contacts/{contact.id}",
            json={"contact_role": "BILLING"},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_update_empty_body_is_noop(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """Empty PATCH body returns 200 with unchanged contact."""
        contact = await _make_contact(db_session, user_factory, sample_org, role="BILLING")

        resp = await client.patch(
            f"{ORGS}/{sample_org.id}/contacts/{contact.id}",
            json={},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["contact_role"] == "BILLING"


# ── Remove contact ─────────────────────────────────────────────────────────────


class TestRemoveOrgContact:
    """DELETE /v1/organizations/{id}/contacts/{cid}"""

    @pytest.mark.asyncio
    async def test_admin_can_remove_contact(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """Admin soft-deletes a contact; it disappears from the list."""
        c1 = await _make_contact(db_session, user_factory, sample_org, role="ACCOUNT_OWNER", is_primary=True)
        c2 = await _make_contact(db_session, user_factory, sample_org, role="BILLING")

        resp = await client.delete(
            f"{ORGS}/{sample_org.id}/contacts/{c2.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Removed contact no longer in list
        list_resp = await client.get(f"{ORGS}/{sample_org.id}/contacts", headers=admin_headers)
        contact_ids = [c["id"] for c in _contacts_from_list_response(list_resp.json()["data"])]
        assert c2.id not in contact_ids

    @pytest.mark.asyncio
    async def test_cannot_remove_last_active_contact(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """Removing the last active contact raises 422."""
        contact = await _make_contact(db_session, user_factory, sample_org, role="ACCOUNT_OWNER", is_primary=True)

        resp = await client.delete(
            f"{ORGS}/{sample_org.id}/contacts/{contact.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_remove_not_found_returns_404(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org: Organization,
    ) -> None:
        """Unknown contact ID → 404."""
        resp = await client.delete(
            f"{ORGS}/{sample_org.id}/contacts/00000000-0000-0000-0000-000000000000",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_remove_requires_admin_or_account_owner(
        self,
        client: AsyncClient,
        auth_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """B2C user → 403."""
        contact = await _make_contact(db_session, user_factory, sample_org)

        resp = await client.delete(
            f"{ORGS}/{sample_org.id}/contacts/{contact.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_remove_is_soft_delete(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """Removed contact has status INACTIVE in DB (not physically deleted)."""
        from sqlalchemy import select

        c1 = await _make_contact(db_session, user_factory, sample_org, role="ACCOUNT_OWNER", is_primary=True)
        c2 = await _make_contact(db_session, user_factory, sample_org, role="BILLING")

        await client.delete(
            f"{ORGS}/{sample_org.id}/contacts/{c2.id}",
            headers=admin_headers,
        )

        result = await db_session.execute(select(OrgContact).where(OrgContact.id == c2.id))
        row = result.scalar_one_or_none()
        assert row is not None
        assert row.status == ContactStatus.INACTIVE


# ── Set primary contact ────────────────────────────────────────────────────────


class TestSetPrimaryContact:
    """POST /v1/organizations/{id}/contacts/{cid}/set-primary"""

    @pytest.mark.asyncio
    async def test_admin_can_set_primary(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """Setting a contact as primary returns that contact with is_primary=True."""
        c1 = await _make_contact(db_session, user_factory, sample_org, role="ACCOUNT_OWNER", is_primary=True)
        c2 = await _make_contact(db_session, user_factory, sample_org, role="BILLING", is_primary=False)

        resp = await client.post(
            f"{ORGS}/{sample_org.id}/contacts/{c2.id}/set-primary",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["is_primary"] is True
        assert resp.json()["data"]["id"] == c2.id

    @pytest.mark.asyncio
    async def test_set_primary_clears_other_contacts(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """After set-primary the old primary is no longer primary."""
        from sqlalchemy import select

        c1 = await _make_contact(db_session, user_factory, sample_org, role="ACCOUNT_OWNER", is_primary=True)
        c2 = await _make_contact(db_session, user_factory, sample_org, role="BILLING", is_primary=False)

        await client.post(
            f"{ORGS}/{sample_org.id}/contacts/{c2.id}/set-primary",
            headers=admin_headers,
        )

        # Refresh c1 from DB
        result = await db_session.execute(select(OrgContact).where(OrgContact.id == c1.id))
        c1_refreshed = result.scalar_one()
        assert c1_refreshed.is_primary is False

    @pytest.mark.asyncio
    async def test_set_primary_not_found_returns_404(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org: Organization,
    ) -> None:
        """Unknown contact ID → 404."""
        resp = await client.post(
            f"{ORGS}/{sample_org.id}/contacts/00000000-0000-0000-0000-000000000000/set-primary",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_set_primary_requires_admin_or_account_owner(
        self,
        client: AsyncClient,
        auth_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """B2C user → 403."""
        contact = await _make_contact(db_session, user_factory, sample_org)

        resp = await client.post(
            f"{ORGS}/{sample_org.id}/contacts/{contact.id}/set-primary",
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_set_primary_idempotent(
        self,
        client: AsyncClient,
        admin_headers: dict,
        db_session: AsyncSession,
        sample_org: Organization,
        user_factory,
    ) -> None:
        """Setting an already-primary contact as primary again returns 200 with no errors."""
        contact = await _make_contact(db_session, user_factory, sample_org, is_primary=True)

        resp = await client.post(
            f"{ORGS}/{sample_org.id}/contacts/{contact.id}/set-primary",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["is_primary"] is True


class TestContactPermissionResources:
    """POST /v1/organizations/{id}/contacts — B2B portal permission resources."""

    @pytest.mark.asyncio
    async def test_add_contact_with_new_b2b_resources_accepted(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org: Organization,
    ) -> None:
        """CARD_PAYMENT, REQUEST_CREDIT, REPORTING are valid Resource values."""
        body = {
            "email": f"b2b-perms-{uuid.uuid4().hex[:8]}@example.com",
            "first_name": "Perms",
            "last_name": "Test",
            "contact_number": "+447700000199",
            "contact_role": "OTHER",
            "permissions": [
                {"resource": "DASHBOARD", "level": 1},
                {"resource": "REQUESTS", "level": 1},
                {"resource": "CARD_PAYMENT", "level": 1},
                {"resource": "REQUEST_CREDIT", "level": 0},
                {"resource": "REPORTING", "level": 1},
                {"resource": "BILLING", "level": 1},
                {"resource": "NOTIFICATIONS", "level": 1},
                {"resource": "DOCUMENTS", "level": 1},
                {"resource": "CONTACTS", "level": 0},
            ],
        }
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(
                f"{ORGS}/{sample_org.id}/contacts",
                json=body,
                headers=admin_headers,
            )
        assert resp.status_code == 201, resp.text

    @pytest.mark.asyncio
    async def test_add_contact_with_invalid_resource_returns_422(
        self,
        client: AsyncClient,
        admin_headers: dict,
        sample_org: Organization,
    ) -> None:
        """Unknown resource values must be rejected with 422."""
        body = {
            "email": f"bad-perms-{uuid.uuid4().hex[:8]}@example.com",
            "first_name": "Bad",
            "last_name": "Perms",
            "contact_number": "+447700000200",
            "contact_role": "OTHER",
            "permissions": [
                {"resource": "NONEXISTENT_RESOURCE", "level": 1},
            ],
        }
        with _mock_enqueue(), _mock_create_invite():
            resp = await client.post(
                f"{ORGS}/{sample_org.id}/contacts",
                json=body,
                headers=admin_headers,
            )
        assert resp.status_code == 422
