"""API and integration tests for team availability (admin settings)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import create_access_token
from app.modules.depots.models import Depot
from app.modules.drivers.enums import DriverAccountStatus
from app.modules.drivers.models import Driver
from app.modules.holidays.enums import HolidayAudience
from app.modules.holidays.models import Holiday
from app.modules.user.models import User

BASE = "/v1/team-availability"
DRIVERS = "/v1/drivers"


def _admin_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="ADMIN", client_type="ADMIN")
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "ADMIN"}


def _driver_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="DRIVER", client_type="DRIVER")
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "DRIVER"}


def _super_admin_headers(user_id: str) -> dict[str, str]:
    token, _ = create_access_token(user_id=user_id, role="SUPER_ADMIN", client_type="ADMIN")
    return {"Authorization": f"Bearer {token}", "X-Client-Type": "ADMIN"}


class TestTeamAvailabilityApi:
    @pytest.mark.asyncio
    async def test_leave_types_list(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(f"{BASE}/leave-types", headers=_admin_headers(admin.id))
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) >= 9
        assert any(i["type"] == "ANNUAL_LEAVE" for i in items)
        sick = next(i for i in items if i["type"] == "SICK_LEAVE")
        assert sick["label"] == "Sick Leave"
        assert sick["color_hex"].startswith("#")

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(f"{BASE}/leave-types")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_calendar_and_who_is_off(
        self,
        client: AsyncClient,
        user_factory,
        driver_user_with_profile: User,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        driver = await db_session.scalar(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
        assert driver is not None

        start = date.today()
        end = start + timedelta(days=2)
        create_resp = await client.post(
            f"{DRIVERS}/{driver.id}/time-off",
            headers=_admin_headers(admin.id),
            data={
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "type": "SICK_LEAVE",
                "notes": "Demo sick leave",
                "is_paid": "true",
            },
        )
        assert create_resp.status_code in (200, 201)
        time_off_id = create_resp.json()["data"]["id"]

        holiday = Holiday(
            year=start.year,
            name="Good Friday",
            start_date=start,
            end_date=start,
            audience=HolidayAudience.BOTH.value,
            allow_shifts=False,
        )
        db_session.add(holiday)
        await db_session.commit()

        cal_resp = await client.get(
            f"{BASE}/calendar",
            headers=_admin_headers(admin.id),
            params={"from_date": start.isoformat(), "to_date": (start + timedelta(days=7)).isoformat()},
        )
        assert cal_resp.status_code == 200
        cal = cal_resp.json()["data"]
        assert cal["summary"]["drivers_on_leave_count"] >= 1
        assert len(cal["leave_entries"]) == 3
        assert any(e["time_off_type"] == "SICK_LEAVE" for e in cal["leave_entries"])
        assert any(e["holiday_name"] == "Good Friday" for e in cal["holiday_entries"])

        who_resp = await client.get(
            f"{BASE}/who-is-off",
            headers=_admin_headers(admin.id),
            params={"from_date": start.isoformat(), "to_date": end.isoformat()},
        )
        assert who_resp.status_code == 200
        who = who_resp.json()["data"]
        assert who["total"] >= 1
        assert who["items"][0]["leave_type_label"] == "Sick Leave"

        detail_resp = await client.get(
            f"{BASE}/time-off/{time_off_id}",
            headers=_admin_headers(admin.id),
        )
        assert detail_resp.status_code == 200
        detail = detail_resp.json()["data"]
        assert detail["notes"] == "Demo sick leave"
        assert detail["duration_days"] >= 1

    @pytest.mark.asyncio
    async def test_calendar_inverted_dates(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(
            f"{BASE}/calendar",
            headers=_admin_headers(admin.id),
            params={"from_date": "2026-04-10", "to_date": "2026-04-01"},
        )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_calendar_range_validation(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        start = date.today()
        end = start + timedelta(days=120)
        resp = await client.get(
            f"{BASE}/calendar",
            headers=_admin_headers(admin.id),
            params={"from_date": start.isoformat(), "to_date": end.isoformat()},
        )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_who_is_off_range_validation(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        start = date.today()
        resp = await client.get(
            f"{BASE}/who-is-off",
            headers=_admin_headers(admin.id),
            params={"from_date": start.isoformat(), "to_date": (start + timedelta(days=20)).isoformat()},
        )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_invalid_time_off_type_filter(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        start = date.today()
        resp = await client.get(
            f"{BASE}/calendar",
            headers=_admin_headers(admin.id),
            params={
                "from_date": start.isoformat(),
                "to_date": start.isoformat(),
                "time_off_type": "NOT_A_LEAVE_TYPE",
            },
        )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_time_off_type_filter_excludes_other_types(
        self,
        client: AsyncClient,
        user_factory,
        driver_user_with_profile: User,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        driver = await db_session.scalar(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
        assert driver is not None
        day = date.today()
        for leave_type in ("SICK_LEAVE", "ANNUAL_LEAVE"):
            await client.post(
                f"{DRIVERS}/{driver.id}/time-off",
                headers=_admin_headers(admin.id),
                data={
                    "start_date": day.isoformat(),
                    "end_date": day.isoformat(),
                    "type": leave_type,
                    "is_paid": "true",
                },
            )

        resp = await client.get(
            f"{BASE}/calendar",
            headers=_admin_headers(admin.id),
            params={
                "from_date": day.isoformat(),
                "to_date": day.isoformat(),
                "time_off_type": "SICK_LEAVE",
                "include_holidays": "false",
            },
        )
        assert resp.status_code == 200
        entries = resp.json()["data"]["leave_entries"]
        assert len(entries) == 1
        assert entries[0]["time_off_type"] == "SICK_LEAVE"

    @pytest.mark.asyncio
    async def test_include_holidays_false(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        day = date.today()
        db_session.add(
            Holiday(
                year=day.year,
                name="Bank Holiday",
                start_date=day,
                end_date=day,
                audience=HolidayAudience.BOTH.value,
                allow_shifts=False,
            )
        )
        await db_session.commit()

        resp = await client.get(
            f"{BASE}/calendar",
            headers=_admin_headers(admin.id),
            params={
                "from_date": day.isoformat(),
                "to_date": day.isoformat(),
                "include_holidays": "false",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["holiday_entries"] == []

    @pytest.mark.asyncio
    async def test_leave_detail_not_found(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(
            f"{BASE}/time-off/{uuid.uuid4()}",
            headers=_admin_headers(admin.id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_only_my_leaves_admin_sees_empty(
        self,
        client: AsyncClient,
        user_factory,
        driver_user_with_profile: User,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        driver = await db_session.scalar(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
        other_user: User = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        other_driver = Driver(
            user_id=other_user.id,
            driver_code=f"DR-{other_user.id[:8].upper()}",
            account_status=DriverAccountStatus.ACTIVE.value,
        )
        db_session.add(other_driver)
        await db_session.flush()

        start = date.today()
        for drv in (driver, other_driver):
            await client.post(
                f"{DRIVERS}/{drv.id}/time-off",
                headers=_admin_headers(admin.id),
                data={
                    "start_date": start.isoformat(),
                    "end_date": start.isoformat(),
                    "type": "ANNUAL_LEAVE",
                    "is_paid": "true",
                },
            )

        empty_resp = await client.get(
            f"{BASE}/calendar",
            headers=_admin_headers(admin.id),
            params={
                "from_date": start.isoformat(),
                "to_date": start.isoformat(),
                "only_my_leaves": "true",
                "include_holidays": "false",
            },
        )
        assert empty_resp.status_code == 200
        assert empty_resp.json()["data"]["leave_entries"] == []

    @pytest.mark.asyncio
    async def test_only_my_leaves_driver_sees_own(
        self,
        client: AsyncClient,
        user_factory,
        driver_user_with_profile: User,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        driver = await db_session.scalar(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
        other_user: User = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True)
        other_driver = Driver(
            user_id=other_user.id,
            driver_code=f"DR-{other_user.id[:8].upper()}",
            account_status=DriverAccountStatus.ACTIVE.value,
        )
        db_session.add(other_driver)
        await db_session.flush()

        start = date.today()
        for drv in (driver, other_driver):
            await client.post(
                f"{DRIVERS}/{drv.id}/time-off",
                headers=_admin_headers(admin.id),
                data={
                    "start_date": start.isoformat(),
                    "end_date": start.isoformat(),
                    "type": "MATERNITY_LEAVE",
                    "is_paid": "true",
                },
            )

        resp = await client.get(
            f"{BASE}/calendar",
            headers=_driver_headers(driver_user_with_profile.id),
            params={
                "from_date": start.isoformat(),
                "to_date": start.isoformat(),
                "only_my_leaves": "true",
                "include_holidays": "false",
            },
        )
        assert resp.status_code == 200
        entries = resp.json()["data"]["leave_entries"]
        assert len(entries) == 1
        assert entries[0]["short_name"] == "You"
        assert entries[0]["is_current_user"] is True

    @pytest.mark.asyncio
    async def test_depot_filter(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        def _depot(code: str, name: str) -> Depot:
            return Depot(
                code=code,
                name=name,
                address_line_1="1 Test Road",
                city="London",
                postcode="E1 1AA",
                timezone="Europe/London",
            )

        depot_a = _depot(f"TA-{uuid.uuid4().hex[:6].upper()}", "Depot A")
        depot_b = _depot(f"TB-{uuid.uuid4().hex[:6].upper()}", "Depot B")
        db_session.add_all([depot_a, depot_b])
        await db_session.flush()

        user_a: User = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True, first_name="Depot", last_name="Alpha")
        user_b: User = await user_factory(role="DRIVER", status="ACTIVE", email_verified=True, first_name="Depot", last_name="Beta")
        driver_a = Driver(
            user_id=user_a.id,
            driver_code=f"DR-{user_a.id[:8].upper()}",
            depot_id=depot_a.id,
            account_status=DriverAccountStatus.ACTIVE.value,
        )
        driver_b = Driver(
            user_id=user_b.id,
            driver_code=f"DR-{user_b.id[:8].upper()}",
            depot_id=depot_b.id,
            account_status=DriverAccountStatus.ACTIVE.value,
        )
        db_session.add_all([driver_a, driver_b])
        await db_session.flush()

        day = date.today()
        for drv in (driver_a, driver_b):
            await client.post(
                f"{DRIVERS}/{drv.id}/time-off",
                headers=_admin_headers(admin.id),
                data={
                    "start_date": day.isoformat(),
                    "end_date": day.isoformat(),
                    "type": "ANNUAL_LEAVE",
                    "is_paid": "true",
                },
            )

        resp = await client.get(
            f"{BASE}/calendar",
            headers=_admin_headers(admin.id),
            params={
                "from_date": day.isoformat(),
                "to_date": day.isoformat(),
                "depot_id": depot_a.id,
                "include_holidays": "false",
            },
        )
        assert resp.status_code == 200
        entries = resp.json()["data"]["leave_entries"]
        assert len(entries) == 1
        assert entries[0]["display_name"] == "Depot Alpha"

    @pytest.mark.asyncio
    async def test_leave_spanning_month_boundary_clipped(
        self,
        client: AsyncClient,
        user_factory,
        driver_user_with_profile: User,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        driver = await db_session.scalar(select(Driver).where(Driver.user_id == driver_user_with_profile.id))
        assert driver is not None

        await client.post(
            f"{DRIVERS}/{driver.id}/time-off",
            headers=_admin_headers(admin.id),
            data={
                "start_date": "2026-03-30",
                "end_date": "2026-04-02",
                "type": "ANNUAL_LEAVE",
                "is_paid": "true",
            },
        )

        resp = await client.get(
            f"{BASE}/calendar",
            headers=_admin_headers(admin.id),
            params={"from_date": "2026-04-01", "to_date": "2026-04-30", "include_holidays": "false"},
        )
        assert resp.status_code == 200
        dates = {e["calendar_date"] for e in resp.json()["data"]["leave_entries"]}
        assert "2026-03-30" not in dates
        assert "2026-04-01" in dates
        assert "2026-04-02" in dates

    @pytest.mark.asyncio
    async def test_draft_driver_excluded_from_calendar(
        self,
        client: AsyncClient,
        user_factory,
        db_session,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        draft_driver = Driver(
            driver_code=f"DR-DRAFT-{uuid.uuid4().hex[:8].upper()}",
            account_status=DriverAccountStatus.DRAFT.value,
            user_id=None,
        )
        db_session.add(draft_driver)
        await db_session.flush()

        day = date.today()
        from app.modules.drivers.models import DriverTimeOff

        db_session.add(
            DriverTimeOff(
                driver_id=draft_driver.id,
                start_date=day,
                end_date=day,
                type="SICK_LEAVE",
                is_paid=True,
            )
        )
        await db_session.commit()

        resp = await client.get(
            f"{BASE}/calendar",
            headers=_admin_headers(admin.id),
            params={"from_date": day.isoformat(), "to_date": day.isoformat(), "include_holidays": "false"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["leave_entries"] == []

    @pytest.mark.asyncio
    async def test_empty_calendar_ok(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        day = date.today()
        resp = await client.get(
            f"{BASE}/calendar",
            headers=_admin_headers(admin.id),
            params={"from_date": day.isoformat(), "to_date": day.isoformat(), "include_holidays": "false"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["summary"]["drivers_on_leave_count"] == 0
        assert data["summary"]["staff_on_leave_count"] == 0
        assert data["leave_entries"] == []


class TestMyLeavesApi:
    @pytest.mark.asyncio
    async def test_my_leaves_crud_and_calendar(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        start = date.today() + timedelta(days=30)
        end = start + timedelta(days=2)

        create_resp = await client.post(
            f"{BASE}/my-leaves",
            headers=headers,
            json={
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "type": "ANNUAL_LEAVE",
                "is_paid": True,
                "notes": "Planned annual leave",
            },
        )
        assert create_resp.status_code == 201
        created = create_resp.json()["data"]
        leave_id = created["id"]
        assert created["duration_label"] == "3 Days"
        assert created["leave_status"] == "PAID"
        assert created["leave_type_label"] == "Annual Leave"

        list_resp = await client.get(f"{BASE}/my-leaves", headers=headers)
        assert list_resp.status_code == 200
        listing = list_resp.json()["data"]
        assert listing["total"] == 1
        assert listing["paid_leave_taken"] >= 3

        cal_resp = await client.get(
            f"{BASE}/calendar",
            headers=headers,
            params={
                "from_date": start.isoformat(),
                "to_date": end.isoformat(),
                "include_holidays": "false",
            },
        )
        assert cal_resp.status_code == 200
        staff_entries = [
            e for e in cal_resp.json()["data"]["leave_entries"] if e.get("member_type") == "STAFF"
        ]
        assert len(staff_entries) == 3
        assert staff_entries[0]["short_name"] == "You"

        detail_resp = await client.get(
            f"{BASE}/time-off/{leave_id}",
            headers=headers,
            params={"member_type": "STAFF"},
        )
        assert detail_resp.status_code == 200
        assert detail_resp.json()["data"]["notes"] == "Planned annual leave"

        patch_resp = await client.patch(
            f"{BASE}/my-leaves/{leave_id}",
            headers=headers,
            json={"notes": "Updated note", "is_paid": False},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["data"]["leave_status"] == "UNPAID"
        assert patch_resp.json()["data"]["notes"] == "Updated note"

        del_resp = await client.delete(f"{BASE}/my-leaves/{leave_id}", headers=headers)
        assert del_resp.status_code == 204

        list_after = await client.get(f"{BASE}/my-leaves", headers=headers)
        assert list_after.json()["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_my_leaves_overlap_rejected(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        day = date.today() + timedelta(days=60)
        payload = {
            "start_date": day.isoformat(),
            "end_date": day.isoformat(),
            "type": "SICK_LEAVE",
            "is_paid": True,
        }
        assert (await client.post(f"{BASE}/my-leaves", headers=headers, json=payload)).status_code == 201
        overlap = await client.post(f"{BASE}/my-leaves", headers=headers, json=payload)
        assert overlap.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_my_leaves_only_own_records(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin_a: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        admin_b: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        day = date.today() + timedelta(days=90)
        create = await client.post(
            f"{BASE}/my-leaves",
            headers=_admin_headers(admin_a.id),
            json={
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
                "type": "EMERGENCY_LEAVE",
                "is_paid": False,
            },
        )
        leave_id = create.json()["data"]["id"]

        forbidden = await client.patch(
            f"{BASE}/my-leaves/{leave_id}",
            headers=_admin_headers(admin_b.id),
            json={"notes": "Hijack"},
        )
        assert forbidden.status_code == 403

    @pytest.mark.asyncio
    async def test_my_leaves_driver_role_forbidden(
        self,
        client: AsyncClient,
        driver_user_with_profile: User,
    ) -> None:
        resp = await client.get(
            f"{BASE}/my-leaves",
            headers=_driver_headers(driver_user_with_profile.id),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_only_my_leaves_admin_sees_staff_leave(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        day = date.today() + timedelta(days=45)
        await client.post(
            f"{BASE}/my-leaves",
            headers=headers,
            json={
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
                "type": "MEDICAL_APPOINTMENT",
                "is_paid": True,
            },
        )
        resp = await client.get(
            f"{BASE}/calendar",
            headers=headers,
            params={
                "from_date": day.isoformat(),
                "to_date": day.isoformat(),
                "only_my_leaves": "true",
                "include_holidays": "false",
            },
        )
        assert resp.status_code == 200
        entries = resp.json()["data"]["leave_entries"]
        assert len(entries) == 1
        assert entries[0]["member_type"] == "STAFF"
        assert entries[0]["short_name"] == "You"

    @pytest.mark.asyncio
    async def test_get_my_leave_by_id(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        day = date.today() + timedelta(days=10)
        create = await client.post(
            f"{BASE}/my-leaves",
            headers=headers,
            json={
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
                "type": "SICK_LEAVE",
                "is_paid": False,
                "notes": "Single day sick",
            },
        )
        assert create.status_code == 201
        leave_id = create.json()["data"]["id"]

        get_resp = await client.get(f"{BASE}/my-leaves/{leave_id}", headers=headers)
        assert get_resp.status_code == 200
        row = get_resp.json()["data"]
        assert row["id"] == leave_id
        assert row["duration_label"] == "Full Day"
        assert row["leave_status"] == "UNPAID"
        assert row["notes"] == "Single day sick"

    @pytest.mark.asyncio
    async def test_my_leaves_create_invalid_date_range(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.post(
            f"{BASE}/my-leaves",
            headers=_admin_headers(admin.id),
            json={
                "start_date": "2026-06-10",
                "end_date": "2026-06-01",
                "type": "ANNUAL_LEAVE",
                "is_paid": True,
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_my_leaves_patch_invalid_date_range(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        day = date.today() + timedelta(days=15)
        create = await client.post(
            f"{BASE}/my-leaves",
            headers=headers,
            json={
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
                "type": "ANNUAL_LEAVE",
                "is_paid": True,
            },
        )
        leave_id = create.json()["data"]["id"]
        patch = await client.patch(
            f"{BASE}/my-leaves/{leave_id}",
            headers=headers,
            json={"start_date": "2026-12-10", "end_date": "2026-12-01"},
        )
        assert patch.status_code == 422

    @pytest.mark.asyncio
    async def test_my_leaves_delete_forbidden_for_other_admin(
        self,
        client: AsyncClient,
        user_factory,
    ) -> None:
        admin_a: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        admin_b: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        day = date.today() + timedelta(days=20)
        create = await client.post(
            f"{BASE}/my-leaves",
            headers=_admin_headers(admin_a.id),
            json={
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
                "type": "ANNUAL_LEAVE",
                "is_paid": True,
            },
        )
        leave_id = create.json()["data"]["id"]
        del_resp = await client.delete(
            f"{BASE}/my-leaves/{leave_id}",
            headers=_admin_headers(admin_b.id),
        )
        assert del_resp.status_code == 403

    @pytest.mark.asyncio
    async def test_staff_leave_detail_not_found(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        resp = await client.get(
            f"{BASE}/time-off/{uuid.uuid4()}",
            headers=_admin_headers(admin.id),
            params={"member_type": "STAFF"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_who_is_off_includes_staff_leave(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(
            role="ADMIN",
            status="ACTIVE",
            email_verified=True,
            first_name="Daniel",
            last_name="Roberts",
        )
        headers = _admin_headers(admin.id)
        day = date.today() + timedelta(days=5)
        await client.post(
            f"{BASE}/my-leaves",
            headers=headers,
            json={
                "start_date": day.isoformat(),
                "end_date": (day + timedelta(days=1)).isoformat(),
                "type": "ANNUAL_LEAVE",
                "is_paid": True,
            },
        )
        resp = await client.get(
            f"{BASE}/who-is-off",
            headers=headers,
            params={"from_date": day.isoformat(), "to_date": (day + timedelta(days=1)).isoformat()},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] >= 1
        staff_rows = [i for i in data["items"] if i["member_type"] == "STAFF"]
        assert len(staff_rows) == 1
        assert staff_rows[0]["display_name"] == "You"
        assert staff_rows[0]["duration_days"] == 2

    @pytest.mark.asyncio
    async def test_calendar_staff_on_leave_count(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        day = date.today() + timedelta(days=7)
        await client.post(
            f"{BASE}/my-leaves",
            headers=headers,
            json={
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
                "type": "EMERGENCY_LEAVE",
                "is_paid": False,
            },
        )
        resp = await client.get(
            f"{BASE}/calendar",
            headers=headers,
            params={
                "from_date": day.isoformat(),
                "to_date": day.isoformat(),
                "include_holidays": "false",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["summary"]["staff_on_leave_count"] == 1

    @pytest.mark.asyncio
    async def test_super_admin_can_create_my_leave(self, client: AsyncClient, user_factory) -> None:
        super_admin: User = await user_factory(role="SUPER_ADMIN", status="ACTIVE", email_verified=True)
        day = date.today() + timedelta(days=25)
        resp = await client.post(
            f"{BASE}/my-leaves",
            headers=_super_admin_headers(super_admin.id),
            json={
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
                "type": "DISCRETIONARY_SPECIAL_LEAVE",
                "is_paid": True,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["leave_type_label"] == "Discretionary & Special Leave"

    @pytest.mark.asyncio
    async def test_my_leaves_unauthenticated_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(f"{BASE}/my-leaves")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_my_leaves_paid_unpaid_totals(self, client: AsyncClient, user_factory) -> None:
        admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
        headers = _admin_headers(admin.id)
        year = date.today().year
        paid_start = date(year, 6, 1)
        unpaid_start = date(year, 7, 1)
        await client.post(
            f"{BASE}/my-leaves",
            headers=headers,
            json={
                "start_date": paid_start.isoformat(),
                "end_date": (paid_start + timedelta(days=2)).isoformat(),
                "type": "ANNUAL_LEAVE",
                "is_paid": True,
            },
        )
        await client.post(
            f"{BASE}/my-leaves",
            headers=headers,
            json={
                "start_date": unpaid_start.isoformat(),
                "end_date": unpaid_start.isoformat(),
                "type": "SICK_LEAVE",
                "is_paid": False,
            },
        )
        list_resp = await client.get(f"{BASE}/my-leaves", headers=headers)
        assert list_resp.status_code == 200
        totals = list_resp.json()["data"]
        assert totals["paid_leave_taken"] == 3
        assert totals["unpaid_leave_taken"] == 1
        assert totals["total"] == 2
