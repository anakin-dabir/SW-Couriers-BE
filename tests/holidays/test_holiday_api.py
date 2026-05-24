"""Integration API tests for Holidays (v1) — list, create, get, update, delete, copy.

All endpoints require SETTINGS permission (READ for list/get, WRITE for create/update/delete/copy).
Uses admin user (has SETTINGS WRITE by default) and per-test transaction rollback.
"""

import uuid

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token
from app.modules.drivers.models import Driver
from app.modules.user.models import User

HOLIDAYS = "/v1/holidays"
HOLIDAYS_YEARS = "/v1/holidays/years"


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
    name: str = "New Year",
    start_date: str = "2025-01-01",
    end_date: str = "2025-01-01",
    audience: str = "BOTH",
    allow_shifts: bool = False,
    allowed_driver_ids: list[str] | None = None,
) -> dict:
    payload: dict = {
        "name": name,
        "start_date": start_date,
        "end_date": end_date,
        "audience": audience,
        "allow_shifts": allow_shifts,
    }
    if allowed_driver_ids is not None:
        payload["allowed_driver_ids"] = allowed_driver_ids
    return payload


# ═══════════════════════════════════════════════════
#  LIST HOLIDAYS — GET /
# ═══════════════════════════════════════════════════


class TestListHolidays:
    """GET /v1/holidays/ — list holidays (SETTINGS READ)."""

    @pytest.mark.asyncio
    async def test_admin_lists_holidays_empty(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(HOLIDAYS + "/", headers=_admin_headers(admin.id), params={"year": 2099})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["items"] == []
        assert data["total"] == 0
        assert data["year"] == 2099

    @pytest.mark.asyncio
    async def test_admin_lists_holidays_with_year_filter(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(HOLIDAYS + "/", headers=_admin_headers(admin.id), params={"year": 2025})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["year"] == 2025
        assert data["items"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_admin_lists_holidays_with_audience_filter(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            params={"audience": "INTERNAL"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["items"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(HOLIDAYS + "/", headers={"X-Client-Type": "ADMIN"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_customer_without_settings_returns_403(self, client: AsyncClient, verified_user: User) -> None:
        resp = await client.get(HOLIDAYS + "/", headers=_customer_headers(verified_user.id))
        assert resp.status_code == 403


class TestListHolidayYears:
    """GET /v1/holidays/years — list years with holiday counts."""

    @pytest.mark.asyncio
    async def test_admin_lists_holiday_year_counts(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(name="Y2024", start_date="2024-06-01", end_date="2024-06-01"),
        )
        await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(name="Y2025-A", start_date="2025-01-01", end_date="2025-01-01"),
        )
        await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(name="Y2025-B", start_date="2025-12-25", end_date="2025-12-25"),
        )

        resp = await client.get(HOLIDAYS_YEARS, headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        by_year = {item["year"]: item["holidays_count"] for item in data["items"]}
        assert by_year[2025] >= 2
        assert by_year[2024] >= 1
        assert data["total"] >= 2

    @pytest.mark.asyncio
    async def test_customer_year_counts_returns_403(self, client: AsyncClient, verified_user: User) -> None:
        resp = await client.get(HOLIDAYS_YEARS, headers=_customer_headers(verified_user.id))
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unauthenticated_year_counts_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(HOLIDAYS_YEARS, headers={"X-Client-Type": "ADMIN"})
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════
#  CREATE HOLIDAY — POST /
# ═══════════════════════════════════════════════════


class TestCreateHoliday:
    """POST /v1/holidays/ — create holiday (SETTINGS WRITE)."""

    @pytest.mark.asyncio
    async def test_admin_creates_holiday(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload(name="Christmas", start_date="2025-12-25", end_date="2025-12-25")
        resp = await client.post(HOLIDAYS + "/", headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["name"] == "Christmas"
        assert data["start_date"] == "2025-12-25"
        assert data["end_date"] == "2025-12-25"
        assert data["audience"] == "BOTH"
        assert data["allow_shifts"] is False
        assert "id" in data

    @pytest.mark.asyncio
    async def test_admin_creates_holiday_with_date_range(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload(
            name="Summer Break",
            start_date="2025-07-01",
            end_date="2025-07-15",
            audience="INTERNAL",
        )
        resp = await client.post(HOLIDAYS + "/", headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["name"] == "Summer Break"
        assert data["start_date"] == "2025-07-01"
        assert data["end_date"] == "2025-07-15"
        assert data["audience"] == "INTERNAL"

    @pytest.mark.asyncio
    async def test_create_end_date_before_start_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload(start_date="2025-01-10", end_date="2025-01-05")
        resp = await client.post(HOLIDAYS + "/", headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 422
        assert "end_date" in resp.json().get("message", "").lower() or "date" in resp.json().get("message", "").lower()

    @pytest.mark.asyncio
    async def test_create_end_date_far_beyond_next_year_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload(start_date="2025-12-31", end_date="2027-01-01")
        resp = await client.post(HOLIDAYS + "/", headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_cross_year_holiday_is_allowed(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload(
            name="Year End Shutdown",
            start_date="2025-12-29",
            end_date="2026-01-01",
        )
        resp = await client.post(HOLIDAYS + "/", headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["start_date"] == "2025-12-29"
        assert data["end_date"] == "2026-01-01"

    @pytest.mark.asyncio
    async def test_create_missing_required_fields_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json={"name": "Partial"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_invalid_audience_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        payload = _valid_create_payload()
        payload["audience"] = "INVALID"
        resp = await client.post(HOLIDAYS + "/", headers=_admin_headers(admin.id), json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_unauthenticated_create_returns_401(self, client: AsyncClient) -> None:
        resp = await client.post(
            HOLIDAYS + "/",
            headers={"X-Client-Type": "ADMIN"},
            json=_valid_create_payload(),
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_customer_create_returns_403(self, client: AsyncClient, verified_user: User) -> None:
        resp = await client.post(
            HOLIDAYS + "/",
            headers=_customer_headers(verified_user.id),
            json=_valid_create_payload(),
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  GET HOLIDAY — GET /{holiday_id}
# ═══════════════════════════════════════════════════


class TestGetHoliday:
    """GET /v1/holidays/{holiday_id} — get single holiday (SETTINGS READ)."""

    @pytest.mark.asyncio
    async def test_admin_gets_holiday(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(name="Easter"),
        )
        assert create_resp.status_code == 201
        holiday_id = create_resp.json()["data"]["id"]

        resp = await client.get(HOLIDAYS + f"/{holiday_id}", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == holiday_id
        assert data["name"] == "Easter"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_404(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(
            HOLIDAYS + "/00000000-0000-0000-0000-000000000000",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unauthenticated_get_returns_401(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(),
        )
        holiday_id = create_resp.json()["data"]["id"]
        resp = await client.get(HOLIDAYS + f"/{holiday_id}", headers={"X-Client-Type": "ADMIN"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_customer_get_returns_403(self, client: AsyncClient, user_factory, verified_user: User) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(),
        )
        holiday_id = create_resp.json()["data"]["id"]
        resp = await client.get(HOLIDAYS + f"/{holiday_id}", headers=_customer_headers(verified_user.id))
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_get_holiday_returns_allowed_driver_name(self, client: AsyncClient, user_factory, db_session) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        driver_user = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True, first_name="Ali", last_name="Raza")
        driver = Driver(user_id=driver_user.id, account_status="ACTIVE", driver_code=f"DR-T-{uuid.uuid4().hex[:8].upper()}")
        db_session.add(driver)
        await db_session.flush()
        await db_session.refresh(driver)

        create_resp = await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(
                name="Named Driver Holiday",
                start_date="2025-11-11",
                end_date="2025-11-11",
                allow_shifts=True,
                allowed_driver_ids=[driver.id],
            ),
        )
        assert create_resp.status_code == 201
        holiday_id = create_resp.json()["data"]["id"]

        resp = await client.get(HOLIDAYS + f"/{holiday_id}", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["allowed_driver_ids"] == [driver.id]
        assert data["allowed_drivers"] == [{"id": driver.id, "name": "Ali Raza"}]


# ═══════════════════════════════════════════════════
#  UPDATE HOLIDAY — PATCH /{holiday_id}
# ═══════════════════════════════════════════════════


class TestUpdateHoliday:
    """PATCH /v1/holidays/{holiday_id} — update holiday (SETTINGS WRITE)."""

    @pytest.mark.asyncio
    async def test_admin_updates_holiday(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(name="Original"),
        )
        assert create_resp.status_code == 201
        holiday_id = create_resp.json()["data"]["id"]

        resp = await client.patch(
            HOLIDAYS + f"/{holiday_id}",
            headers=_admin_headers(admin.id),
            json={"name": "Updated Name", "audience": "EXTERNAL"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["name"] == "Updated Name"
        assert data["audience"] == "EXTERNAL"

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_404(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.patch(
            HOLIDAYS + "/00000000-0000-0000-0000-000000000000",
            headers=_admin_headers(admin.id),
            json={"name": "Any"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_end_date_before_start_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(start_date="2025-06-01", end_date="2025-06-15"),
        )
        holiday_id = create_resp.json()["data"]["id"]
        resp = await client.patch(
            HOLIDAYS + f"/{holiday_id}",
            headers=_admin_headers(admin.id),
            json={"start_date": "2025-06-20", "end_date": "2025-06-10"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_customer_update_returns_403(self, client: AsyncClient, user_factory, verified_user: User) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(),
        )
        holiday_id = create_resp.json()["data"]["id"]
        resp = await client.patch(
            HOLIDAYS + f"/{holiday_id}",
            headers=_customer_headers(verified_user.id),
            json={"name": "Hacked"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_update_allowed_driver_ids_replaces_previous_list(self, client: AsyncClient, user_factory, db_session) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        driver_user_1 = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True, first_name="Driver", last_name="One")
        driver_user_2 = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True, first_name="Driver", last_name="Two")
        driver_1 = Driver(user_id=driver_user_1.id, account_status="ACTIVE", driver_code=f"DR-T-{uuid.uuid4().hex[:8].upper()}")
        driver_2 = Driver(user_id=driver_user_2.id, account_status="ACTIVE", driver_code=f"DR-T-{uuid.uuid4().hex[:8].upper()}")
        db_session.add_all([driver_1, driver_2])
        await db_session.flush()
        await db_session.refresh(driver_1)
        await db_session.refresh(driver_2)
        driver_1_id = driver_1.id
        driver_2_id = driver_2.id

        create_resp = await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(
                name="Replace Drivers",
                start_date="2025-08-01",
                end_date="2025-08-01",
                allow_shifts=True,
                allowed_driver_ids=[driver_1_id],
            ),
        )
        assert create_resp.status_code == 201
        holiday_id = create_resp.json()["data"]["id"]

        update_resp = await client.patch(
            HOLIDAYS + f"/{holiday_id}",
            headers=_admin_headers(admin.id),
            json={"allow_shifts": True, "allowed_driver_ids": [driver_2_id]},
        )
        assert update_resp.status_code == 200
        data = update_resp.json()["data"]
        assert data["allowed_driver_ids"] == [driver_2_id]
        assert data["allowed_drivers"] == [{"id": driver_2_id, "name": "Driver Two"}]

    @pytest.mark.asyncio
    async def test_update_without_allowed_driver_ids_keeps_previous_list(self, client: AsyncClient, user_factory, db_session) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        driver_user = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True, first_name="Carry", last_name="Forward")
        driver = Driver(user_id=driver_user.id, account_status="ACTIVE", driver_code=f"DR-T-{uuid.uuid4().hex[:8].upper()}")
        db_session.add(driver)
        await db_session.flush()
        await db_session.refresh(driver)
        driver_id = driver.id

        create_resp = await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(
                name="Keep Drivers",
                start_date="2025-09-01",
                end_date="2025-09-01",
                allow_shifts=True,
                allowed_driver_ids=[driver_id],
            ),
        )
        assert create_resp.status_code == 201
        holiday_id = create_resp.json()["data"]["id"]

        update_resp = await client.patch(
            HOLIDAYS + f"/{holiday_id}",
            headers=_admin_headers(admin.id),
            json={"name": "Keep Drivers Updated"},
        )
        assert update_resp.status_code == 200
        data = update_resp.json()["data"]
        assert data["allowed_driver_ids"] == [driver_id]
        assert data["allowed_drivers"] == [{"id": driver_id, "name": "Carry Forward"}]


# ═══════════════════════════════════════════════════
#  DELETE HOLIDAY — DELETE /{holiday_id}
# ═══════════════════════════════════════════════════


class TestDeleteHoliday:
    """DELETE /v1/holidays/{holiday_id} — delete holiday (SETTINGS WRITE)."""

    @pytest.mark.asyncio
    async def test_admin_deletes_holiday(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(name="To Delete"),
        )
        assert create_resp.status_code == 201
        holiday_id = create_resp.json()["data"]["id"]

        resp = await client.delete(HOLIDAYS + f"/{holiday_id}", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        assert resp.json()["data"] == {}

        get_resp = await client.get(HOLIDAYS + f"/{holiday_id}", headers=_admin_headers(admin.id))
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.delete(
            HOLIDAYS + "/00000000-0000-0000-0000-000000000000",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_customer_delete_returns_403(self, client: AsyncClient, user_factory, verified_user: User) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(),
        )
        holiday_id = create_resp.json()["data"]["id"]
        resp = await client.delete(HOLIDAYS + f"/{holiday_id}", headers=_customer_headers(verified_user.id))
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  COPY HOLIDAYS — POST /copy
# ═══════════════════════════════════════════════════


class TestCopyHolidays:
    """POST /v1/holidays/copy — copy holidays from one year to another (SETTINGS WRITE)."""

    @pytest.mark.asyncio
    async def test_admin_copies_holidays(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(name="New Year 2025", start_date="2025-01-01", end_date="2025-01-01"),
        )
        await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(name="Easter 2025", start_date="2025-04-20", end_date="2025-04-20"),
        )

        resp = await client.post(
            HOLIDAYS + "/copy",
            headers=_admin_headers(admin.id),
            json={"source_year": 2025, "target_year": 2026},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source_year"] == 2025
        assert data["target_year"] == 2026
        assert data["copied_count"] == 2

        list_resp = await client.get(HOLIDAYS + "/", headers=_admin_headers(admin.id), params={"year": 2026})
        assert list_resp.status_code == 200
        assert list_resp.json()["data"]["total"] == 2

    @pytest.mark.asyncio
    async def test_copy_same_year_returns_422(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.post(
            HOLIDAYS + "/copy",
            headers=_admin_headers(admin.id),
            json={"source_year": 2025, "target_year": 2025},
        )
        assert resp.status_code == 422
        assert "different" in resp.json().get("message", "").lower() or "source" in resp.json().get("message", "").lower()

    @pytest.mark.asyncio
    async def test_copy_empty_source_returns_zero(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.post(
            HOLIDAYS + "/copy",
            headers=_admin_headers(admin.id),
            json={"source_year": 2030, "target_year": 2031},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["copied_count"] == 0

    @pytest.mark.asyncio
    async def test_copy_uses_explicit_year_contract(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(name="Autumn Holiday", start_date="2025-10-15", end_date="2025-10-15"),
        )

        resp = await client.post(
            HOLIDAYS + "/copy",
            headers=_admin_headers(admin.id),
            json={"source_year": 2025, "target_year": 2027},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source_year"] == 2025
        assert data["target_year"] == 2027
        assert data["copied_count"] == 1

    @pytest.mark.asyncio
    async def test_copy_cross_year_holiday_preserves_year_boundary(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        create_resp = await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(
                name="Year End Shutdown",
                start_date="2025-12-29",
                end_date="2026-01-01",
            ),
        )
        assert create_resp.status_code == 201

        resp = await client.post(
            HOLIDAYS + "/copy",
            headers=_admin_headers(admin.id),
            json={"source_year": 2025, "target_year": 2026},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["copied_count"] == 1

        list_resp = await client.get(HOLIDAYS + "/", headers=_admin_headers(admin.id), params={"year": 2026})
        assert list_resp.status_code == 200
        copied = list_resp.json()["data"]["items"][0]
        assert copied["start_date"] == "2026-12-29"
        assert copied["end_date"] == "2027-01-01"

    @pytest.mark.asyncio
    async def test_unauthenticated_copy_returns_401(self, client: AsyncClient) -> None:
        resp = await client.post(
            HOLIDAYS + "/copy",
            headers={"X-Client-Type": "ADMIN"},
            json={"source_year": 2025, "target_year": 2026},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_customer_copy_returns_403(self, client: AsyncClient, verified_user: User) -> None:
        resp = await client.post(
            HOLIDAYS + "/copy",
            headers=_customer_headers(verified_user.id),
            json={"source_year": 2025, "target_year": 2026},
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════
#  LIST AFTER CRUD (integration)
# ═══════════════════════════════════════════════════


class TestListHolidaysWithData:
    """List returns created holidays and respects filters."""

    @pytest.mark.asyncio
    async def test_list_returns_created_holidays(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(name="H1", start_date="2025-01-02", end_date="2025-01-02"),
        )
        await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(name="H2", start_date="2025-01-03", end_date="2025-01-03"),
        )

        resp = await client.get(HOLIDAYS + "/", headers=_admin_headers(admin.id), params={"year": 2025})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 2
        names = {item["name"] for item in data["items"]}
        assert names == {"H1", "H2"}

    @pytest.mark.asyncio
    async def test_list_filter_by_audience(self, client: AsyncClient, user_factory) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(name="Internal Only", start_date="2025-02-01", end_date="2025-02-01", audience="INTERNAL"),
        )
        await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(name="External Only", start_date="2025-02-02", end_date="2025-02-02", audience="EXTERNAL"),
        )

        resp = await client.get(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            params={"year": 2025, "audience": "INTERNAL"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["name"] == "Internal Only"
        assert data["items"][0]["audience"] == "INTERNAL"

    @pytest.mark.asyncio
    async def test_list_includes_allowed_driver_names(self, client: AsyncClient, user_factory, db_session) -> None:
        admin = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        driver_user = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True, first_name="List", last_name="Driver")
        driver = Driver(user_id=driver_user.id, account_status="ACTIVE", driver_code=f"DR-T-{uuid.uuid4().hex[:8].upper()}")
        db_session.add(driver)
        await db_session.flush()
        await db_session.refresh(driver)

        create_resp = await client.post(
            HOLIDAYS + "/",
            headers=_admin_headers(admin.id),
            json=_valid_create_payload(
                name="List Driver Holiday",
                start_date="2025-05-05",
                end_date="2025-05-05",
                allow_shifts=True,
                allowed_driver_ids=[driver.id],
            ),
        )
        assert create_resp.status_code == 201

        resp = await client.get(HOLIDAYS + "/", headers=_admin_headers(admin.id), params={"year": 2025})
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        target = next(item for item in items if item["name"] == "List Driver Holiday")
        assert target["allowed_driver_ids"] == [driver.id]
        assert target["allowed_drivers"] == [{"id": driver.id, "name": "List Driver"}]
