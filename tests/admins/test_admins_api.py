"""Integration API tests — Admin Management endpoints.

Covers:
  POST   /v1/admins                       create admin (send_invite=true/false)
  GET    /v1/admins                       list with pagination, search, filter, sort
  GET    /v1/admins/{id}                  read single admin detail + permissions
  PATCH  /v1/admins/{id}                  update profile (message-only; GET for detail)
  PATCH  /v1/admins/{id}/permissions     replace permission overrides (message-only; GET for detail)
  POST   /v1/admins/{id}/suspend          suspend active admin (ADMINS WRITE; not self)
  POST   /v1/admins/{id}/reactivate       reactivate suspended admin (ADMINS WRITE; not self)
  POST   /v1/admins/{id}/invite           send invite to draft admin

All tests use per-test DB session. Arq and email are mocked.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.user.models import User

ADMINS = "/v1/admins"


# ── Token helpers ─────────────────────────────────────────────────────────────


def _headers(user: User, role: str | None = None) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user.id,
        role=role or user.role,
        client_type="ADMIN",
    )
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "ADMIN"}


def _mock_enqueue():
    return patch("app.modules.admins.v1.routes.enqueue", new_callable=AsyncMock, return_value=MagicMock())


# ── Shared payload ────────────────────────────────────────────────────────────

_BASE_PERMISSIONS = [
    {"resource": "DASHBOARD", "level": "WRITE"},
    {"resource": "SHIPMENTS", "level": "WRITE"},
    {"resource": "WAREHOUSES", "level": "WRITE"},
    {"resource": "DRIVERS", "level": "WRITE"},
    {"resource": "CUSTOMERS", "level": "WRITE"},
    {"resource": "ORGANIZATIONS", "level": "WRITE"},
    {"resource": "INVOICES", "level": "WRITE"},
    {"resource": "REPORTS", "level": "WRITE"},
    {"resource": "REGIONS", "level": "WRITE"},
    {"resource": "USERS", "level": "WRITE"},
    {"resource": "ADMINS", "level": "WRITE"},
    {"resource": "RESET_ADMIN_PASSWORDS", "level": "WRITE"},
    {"resource": "RESET_B2B_CLIENT_PASSWORDS", "level": "WRITE"},
    {"resource": "AUDIT_LOG", "level": "READ"},
    {"resource": "SETTINGS", "level": "WRITE"},
    {"resource": "SUPPORT_TICKETS", "level": "WRITE"},
    {"resource": "VEHICLE_MANAGEMENT", "level": "WRITE"},
    {"resource": "HOLIDAYS", "level": "WRITE"},
    {"resource": "SUSPENSION_RULES", "level": "WRITE"},
    {"resource": "SYSTEM_DEFAULTS", "level": "WRITE"},
    {"resource": "SERVICE_TIERS", "level": "WRITE"},
    {"resource": "ACCESS_LOGS", "level": "READ"},
    {"resource": "DOCUMENTS", "level": "WRITE"},
    {"resource": "BILLING", "level": "WRITE"},
    {"resource": "CREDIT_NOTES", "level": "WRITE"},
    {"resource": "ROUTE_PLANNING", "level": "WRITE"},
    {"resource": "REQUESTS", "level": "NONE"},
    {"resource": "NOTIFICATIONS", "level": "NONE"},
    {"resource": "CONTACTS", "level": "NONE"},
]


def _create_payload(**overrides) -> dict:
    base = {
        "title": "MR",
        "first_name": "John",
        "last_name": "Doe",
        "email": "john.doe@example.com",
        "phone": "+44 7911 123456",
        "position_role": "Operations Manager",
        "address_line_1": "99 Admin Way",
        "address_line_2": None,
        "city": "Bristol",
        "state": "England",
        "postcode": "BS1 1AA",
        "permissions": _BASE_PERMISSIONS,
        "send_invite": False,
    }
    base.update(overrides)
    return base


def _create_form_data(**overrides) -> dict[str, str]:
    p = _create_payload(**overrides)
    out: dict[str, str] = {}
    for key, val in p.items():
        if key == "permissions":
            out[key] = json.dumps(p["permissions"])
        elif key == "send_invite":
            out[key] = str(p["send_invite"]).lower()
        elif val is None:
            continue
        else:
            out[key] = str(val)
    return out


# ═══════════════════════════════════════════════════
#  CREATE  POST /admins
# ═══════════════════════════════════════════════════


class TestCreateAdmin:
    @pytest.mark.asyncio
    async def test_create_draft_returns_201(
        self, client: AsyncClient, user_factory
    ) -> None:
        """ADMIN can create a draft admin (send_invite=false)."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

        with _mock_enqueue():
            resp = await client.post(
                ADMINS,
                data=_create_form_data(email="newadmin1@example.com", send_invite=False),
                headers=_headers(admin),
            )

        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["email"] == "newadmin1@example.com"
        assert data["status"] == "PENDING_VERIFICATION"
        assert data["invite_id"] is None

    @pytest.mark.asyncio
    async def test_create_with_invite_enqueues_email(
        self, client: AsyncClient, user_factory
    ) -> None:
        """send_invite=true creates admin and enqueues invite email."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

        with _mock_enqueue() as mock_enqueue:
            resp = await client.post(
                ADMINS,
                data=_create_form_data(email="newadmin2@example.com", send_invite=True),
                headers=_headers(admin),
            )

        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["invite_id"] is not None
        mock_enqueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_duplicate_email_returns_409(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Creating an admin with an already-registered email returns 409."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        existing = await user_factory(email="taken@example.com", status="ACTIVE", email_verified=True)

        with _mock_enqueue():
            resp = await client.post(
                ADMINS,
                data=_create_form_data(email=existing.email, send_invite=False),
                headers=_headers(admin),
            )

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_create_invalid_resource_returns_422(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Invalid permission resource name returns 422."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

        payload = _create_payload(
            email="newadmin3@example.com",
            permissions=[{"resource": "DOES_NOT_EXIST", "level": "WRITE"}],
            send_invite=False,
        )

        with _mock_enqueue():
            resp = await client.post(
                ADMINS,
                data=_create_form_data(
                    email=payload["email"],
                    permissions=payload["permissions"],
                    send_invite=payload["send_invite"],
                ),
                headers=_headers(admin),
            )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_requires_admin_role(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Non-admin users cannot create admins (wrong audience → 401)."""
        driver = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)

        with _mock_enqueue():
            resp = await client.post(
                ADMINS,
                data=_create_form_data(email="newadmin4@example.com"),
                headers=_headers(driver),
            )

        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_super_admin_can_create(
        self, client: AsyncClient, user_factory
    ) -> None:
        """SUPER_ADMIN can also create admins."""
        super_admin = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)

        with _mock_enqueue():
            resp = await client.post(
                ADMINS,
                data=_create_form_data(email="newadmin5@example.com", send_invite=False),
                headers=_headers(super_admin),
            )

        assert resp.status_code == 201, resp.text


# ═══════════════════════════════════════════════════
#  LIST  GET /admins
# ═══════════════════════════════════════════════════


class TestListAdmins:
    @pytest.mark.asyncio
    async def test_list_returns_only_admins(
        self, client: AsyncClient, user_factory
    ) -> None:
        """List endpoint returns only ADMIN/SUPER_ADMIN role users."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        await user_factory(role="ADMIN", status="ACTIVE", email_verified=True, email="admin2@example.com")
        await user_factory(role="DRIVER", status="ACTIVE", email_verified=True, email="driver@example.com")

        resp = await client.get(ADMINS, headers=_headers(admin))

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        roles = {item["role"] for item in data["items"]}
        assert roles <= {"ADMIN", "SUPER_ADMIN"}

    @pytest.mark.asyncio
    async def test_list_pagination(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Pagination page/size params work correctly."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

        resp = await client.get(f"{ADMINS}?page=1&size=1", headers=_headers(admin))

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["items"]) <= 1
        assert data["page"] == 1
        assert data["size"] == 1

    @pytest.mark.asyncio
    async def test_list_search_by_email(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Search param filters by email."""
        admin = await user_factory(
            role="ADMIN", status="ACTIVE", email_verified=True, email="searchable@example.com"
        )

        resp = await client.get(f"{ADMINS}?search=searchable", headers=_headers(admin))

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert any("searchable" in item["email"] for item in data["items"])

    @pytest.mark.asyncio
    async def test_list_filter_by_status(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Status filter returns only matching status."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        await user_factory(
            role="ADMIN", status="SUSPENDED", email_verified=True, email="suspended@example.com"
        )

        resp = await client.get(f"{ADMINS}?status=SUSPENDED", headers=_headers(admin))

        assert resp.status_code == 200
        data = resp.json()["data"]
        for item in data["items"]:
            assert item["status"] == "SUSPENDED"

    @pytest.mark.asyncio
    async def test_list_sort_name_asc(
        self, client: AsyncClient, user_factory
    ) -> None:
        """sort=name_asc returns 200 without error."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

        resp = await client.get(f"{ADMINS}?sort=name_asc", headers=_headers(admin))

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_requires_auth(self, client: AsyncClient) -> None:
        """Unauthenticated request returns 401."""
        resp = await client.get(ADMINS)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_forbidden_for_non_admin(
        self, client: AsyncClient, user_factory
    ) -> None:
        """DRIVER cannot list admins (wrong audience → 401)."""
        driver = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)

        resp = await client.get(ADMINS, headers=_headers(driver))

        assert resp.status_code in (401, 403)


# ═══════════════════════════════════════════════════
#  GET SINGLE  GET /admins/{id}
# ═══════════════════════════════════════════════════


class TestGetAdmin:
    @pytest.mark.asyncio
    async def test_get_returns_detail(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Returns full admin detail including permissions."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(
            role="ADMIN",
            status="ACTIVE",
            email_verified=True,
            email="target@example.com",
            first_name="Jane",
            last_name="Smith",
        )

        resp = await client.get(f"{ADMINS}/{target.id}", headers=_headers(admin))

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["id"] == target.id
        assert data["email"] == "target@example.com"
        assert data["full_name"] == "Jane Smith"
        assert data["role"] == "ADMIN"
        assert "permissions" in data
        assert "last_login" in data

    @pytest.mark.asyncio
    async def test_get_non_admin_user_returns_404(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Getting a non-admin user via this endpoint returns 404."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        driver = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True, email="d@example.com")

        resp = await client.get(f"{ADMINS}/{driver.id}", headers=_headers(admin))

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_unknown_id_returns_404(
        self, client: AsyncClient, user_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

        resp = await client.get(
            f"{ADMINS}/00000000-0000-0000-0000-000000000000", headers=_headers(admin)
        )

        assert resp.status_code == 404


# ═══════════════════════════════════════════════════
#  UPDATE  PATCH /admins/{id}
# ═══════════════════════════════════════════════════


class TestUpdateAdmin:
    @pytest.mark.asyncio
    async def test_update_profile_fields(
        self, client: AsyncClient, user_factory
    ) -> None:
        """ADMIN can update profile fields on another admin."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(
            role="ADMIN",
            status="ACTIVE",
            email_verified=True,
            email="updatable@example.com",
        )

        resp = await client.patch(
            f"{ADMINS}/{target.id}",
            data={"first_name": "Updated", "position_role": "Senior Manager"},
            headers=_headers(admin),
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["message"] == "Admin updated successfully."
        assert "data" not in body

        detail = await client.get(f"{ADMINS}/{target.id}", headers=_headers(admin))
        assert detail.status_code == 200, detail.text
        d = detail.json()["data"]
        assert d["first_name"] == "Updated"
        assert d["position_role"] == "Senior Manager"

    @pytest.mark.asyncio
    async def test_update_permissions(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Providing permissions list replaces existing overrides."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(
            role="ADMIN", status="ACTIVE", email_verified=True, email="perms@example.com"
        )

        resp = await client.patch(
            f"{ADMINS}/{target.id}/permissions",
            json={"permissions": [{"resource": "DRIVERS", "level": "READ"}]},
            headers=_headers(admin),
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["message"] == "Admin permissions updated successfully."
        assert "data" not in body

    @pytest.mark.asyncio
    async def test_cannot_patch_own_permissions(
        self, client: AsyncClient, user_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

        resp = await client.patch(
            f"{ADMINS}/{admin.id}/permissions",
            json={"permissions": [{"resource": "DRIVERS", "level": "WRITE"}]},
            headers=_headers(admin),
        )

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_update_non_admin_returns_404(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Trying to update a non-admin user returns 404."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        driver = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True, email="d2@example.com")

        resp = await client.patch(
            f"{ADMINS}/{driver.id}",
            data={"first_name": "Hacked"},
            headers=_headers(admin),
        )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_requires_auth(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

        resp = await client.patch(f"{ADMINS}/{admin.id}", data={"first_name": "X"})

        assert resp.status_code == 401


# ═══════════════════════════════════════════════════
#  SUSPEND  POST /admins/{id}/suspend
# ═══════════════════════════════════════════════════


class TestSuspendAdmin:
    @pytest.mark.asyncio
    async def test_super_admin_can_suspend_active_admin(
        self, client: AsyncClient, user_factory
    ) -> None:
        """SUPER_ADMIN can suspend an ACTIVE admin."""
        super_admin = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(
            role="ADMIN", status="ACTIVE", email_verified=True, email="suspend_me@example.com"
        )

        resp = await client.post(
            f"{ADMINS}/{target.id}/suspend",
            json={"reason": "Policy violation"},
            headers=_headers(super_admin),
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "SUSPENDED"

    @pytest.mark.asyncio
    async def test_plain_admin_can_suspend_active_admin(
        self, client: AsyncClient, user_factory
    ) -> None:
        """ADMIN with default permissions can suspend another ACTIVE admin."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(
            role="ADMIN", status="ACTIVE", email_verified=True, email="target_s@example.com"
        )

        resp = await client.post(
            f"{ADMINS}/{target.id}/suspend",
            json={"reason": "Test"},
            headers=_headers(admin),
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "SUSPENDED"

    @pytest.mark.asyncio
    async def test_suspend_already_suspended_returns_409(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Suspending an already-suspended admin returns 409."""
        super_admin = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(
            role="ADMIN", status="SUSPENDED", email_verified=True, email="already_suspended@example.com"
        )

        resp = await client.post(
            f"{ADMINS}/{target.id}/suspend",
            json={"reason": "Already suspended"},
            headers=_headers(super_admin),
        )

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_cannot_suspend_self(
        self, client: AsyncClient, user_factory
    ) -> None:
        """SUPER_ADMIN cannot suspend their own account."""
        super_admin = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)

        resp = await client.post(
            f"{ADMINS}/{super_admin.id}/suspend",
            json={"reason": "Self suspension"},
            headers=_headers(super_admin),
        )

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_suspend_missing_reason_returns_422(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Missing reason field returns 422."""
        super_admin = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(
            role="ADMIN", status="ACTIVE", email_verified=True, email="noreason@example.com"
        )

        resp = await client.post(
            f"{ADMINS}/{target.id}/suspend",
            json={},
            headers=_headers(super_admin),
        )

        assert resp.status_code == 422


# ═══════════════════════════════════════════════════
#  REACTIVATE  POST /admins/{id}/reactivate
# ═══════════════════════════════════════════════════


class TestReactivateAdmin:
    @pytest.mark.asyncio
    async def test_super_admin_can_reactivate_suspended_admin(
        self, client: AsyncClient, user_factory
    ) -> None:
        """SUPER_ADMIN can reactivate a SUSPENDED admin."""
        super_admin = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(
            role="ADMIN", status="SUSPENDED", email_verified=True, email="reactivate_me@example.com"
        )

        resp = await client.post(
            f"{ADMINS}/{target.id}/reactivate",
            json={"reason": "Issue resolved"},
            headers=_headers(super_admin),
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_plain_admin_can_reactivate_suspended_admin(
        self, client: AsyncClient, user_factory
    ) -> None:
        """ADMIN with default permissions can reactivate a SUSPENDED admin."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(
            role="ADMIN", status="SUSPENDED", email_verified=True, email="target_r@example.com"
        )

        resp = await client.post(
            f"{ADMINS}/{target.id}/reactivate",
            json={"reason": "Test"},
            headers=_headers(admin),
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_cannot_reactivate_self(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Cannot call reactivate on your own user id (even while ACTIVE)."""
        super_admin = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)

        resp = await client.post(
            f"{ADMINS}/{super_admin.id}/reactivate",
            json={"reason": "Self reactivate"},
            headers=_headers(super_admin),
        )

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_reactivate_active_admin_returns_409(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Reactivating an already-active admin returns 409."""
        super_admin = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)
        target = await user_factory(
            role="ADMIN", status="ACTIVE", email_verified=True, email="already_active@example.com"
        )

        resp = await client.post(
            f"{ADMINS}/{target.id}/reactivate",
            json={"reason": "Not suspended"},
            headers=_headers(super_admin),
        )

        assert resp.status_code == 409


# ═══════════════════════════════════════════════════
#  SEND INVITE  POST /admins/{id}/invite
# ═══════════════════════════════════════════════════


class TestSendAdminInvite:
    @pytest.mark.asyncio
    async def test_send_invite_to_draft_admin(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Can send invite to a PENDING_VERIFICATION admin."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        draft = await user_factory(
            role="ADMIN",
            status="PENDING_VERIFICATION",
            email_verified=False,
            email="draft@example.com",
        )

        with _mock_enqueue():
            resp = await client.post(
                f"{ADMINS}/{draft.id}/invite",
                headers=_headers(admin),
            )

        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["email"] == "draft@example.com"
        assert "invite_id" in data

    @pytest.mark.asyncio
    async def test_send_invite_to_active_admin_returns_409(
        self, client: AsyncClient, user_factory
    ) -> None:
        """Cannot send invite to an already-active admin."""
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        active = await user_factory(
            role="ADMIN", status="ACTIVE", email_verified=True, email="active_admin@example.com"
        )

        with _mock_enqueue():
            resp = await client.post(
                f"{ADMINS}/{active.id}/invite",
                headers=_headers(admin),
            )

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_send_invite_unknown_user_returns_404(
        self, client: AsyncClient, user_factory
    ) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)

        with _mock_enqueue():
            resp = await client.post(
                f"{ADMINS}/00000000-0000-0000-0000-000000000000/invite",
                headers=_headers(admin),
            )

        assert resp.status_code == 404
