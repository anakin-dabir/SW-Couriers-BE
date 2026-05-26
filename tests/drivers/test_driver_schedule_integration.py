"""Integration and edge-case tests for driver schedule (holidays, sync, work-schedule)."""

from __future__ import annotations

import uuid
from datetime import date, time, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import create_access_token
from app.modules.drivers.enums import ShiftOrigin
from app.modules.drivers.models import Driver, DriverShift
from app.modules.drivers.repository import (
    DriverRepository,
    DriverShiftRepository,
    DriverWeeklyScheduleRepository,
)
from app.modules.drivers.schedule.context import ScheduleContext
from app.modules.drivers.schedule.sync import sync_shifts_from_weekly_template
from app.modules.drivers.schedule.work_schedule import get_driver_work_schedule
from app.modules.user.models import User
from app.modules.user.repository import UserRepository
from tests.drivers.test_drivers_api import (
    DRIVERS,
    _admin_headers,
    _licence_files,
    _minimal_driver_form,
)
from tests.drivers.test_driver_self_api import DRIVER_PROFILE
from tests.holidays.test_holiday_api import HOLIDAYS, _valid_create_payload

NOTIFY_PATCH = "app.modules.drivers.schedule.notify.notify"


async def _create_internal_driver(
    client: AsyncClient,
    user_factory,
    *,
    email_suffix: str | None = None,
) -> tuple[str, str, dict[str, str]]:
    """Return (driver_id, user_id, admin_headers)."""
    admin: User = await user_factory(role="ADMIN", status="ACTIVE", email_verified=True)
    headers = _admin_headers(admin.id)
    email = f"sched-{email_suffix or uuid.uuid4().hex[:8]}@example.com"
    with patch("app.modules.auth.service.enqueue", new_callable=AsyncMock, return_value=None):
        resp = await client.post(
            f"{DRIVERS}/add-new-driver",
            headers=headers,
            data=_minimal_driver_form(email, driver_type="INTERNAL"),
            files=_licence_files(),
        )
    assert resp.status_code == 201
    driver = resp.json()["data"]["driver"]
    return driver["id"], driver["user_id"], headers


def _next_weekday(target_weekday: int) -> date:
    """Next calendar date with given weekday (0=Monday)."""
    today = date.today()
    days_ahead = (target_weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def _weekly_put_body(*, active_dows: list[int]) -> dict:
    days = []
    for dow in range(7):
        if dow in active_dows:
            days.append(
                {"day_of_week": dow, "is_active": True, "start_time": "09:00:00", "end_time": "17:00:00"}
            )
        else:
            days.append(
                {"day_of_week": dow, "is_active": False, "start_time": None, "end_time": None}
            )
    return {"days": days, "total_weekly_hours": 8.0 * len(active_dows)}


class TestScheduleHolidayRulesUnit:
    """Pure unit tests (no HTTP)."""

    def test_blocks_empty_allow_list(self) -> None:
        from types import SimpleNamespace

        from app.modules.drivers.schedule.holiday_rules import holiday_blocks_driver

        h = SimpleNamespace(
            allow_shifts=True,
            allowed_drivers=[],
        )
        assert holiday_blocks_driver("any-driver", h) is True


class TestWeeklyShiftSync:
    """Weekly PUT materializes WEEKLY_TEMPLATE shifts; MANUAL rows are preserved."""

    @pytest.mark.asyncio
    async def test_weekly_put_creates_template_shifts(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        driver_id, _, headers = await _create_internal_driver(client, user_factory)
        monday = _next_weekday(0)

        with patch(NOTIFY_PATCH, new_callable=AsyncMock, return_value=True):
            put = await client.put(
                f"{DRIVERS}/{driver_id}/schedule",
                headers=headers,
                json=_weekly_put_body(active_dows=[0]),
            )
        assert put.status_code == 200

        shifts = (
            await db_session.execute(
                select(DriverShift).where(
                    DriverShift.driver_id == driver_id,
                    DriverShift.shift_date >= date.today(),
                )
            )
        ).scalars().all()
        template_shifts = [s for s in shifts if s.origin == ShiftOrigin.WEEKLY_TEMPLATE.value]
        assert len(template_shifts) >= 1
        monday_shifts = [s for s in template_shifts if s.shift_date.weekday() == 0]
        assert monday_shifts

    @pytest.mark.asyncio
    async def test_manual_shift_not_overwritten_by_weekly_sync(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        driver_id, _, headers = await _create_internal_driver(client, user_factory)
        target = _next_weekday(0)

        manual = await client.post(
            f"{DRIVERS}/shifts",
            headers=headers,
            data={
                "driver_id": driver_id,
                "date": str(target),
                "start_time": "10:00:00",
                "end_time": "18:00:00",
            },
        )
        assert manual.status_code == 201
        manual_id = manual.json()["data"]["id"]

        with patch(NOTIFY_PATCH, new_callable=AsyncMock, return_value=True):
            put = await client.put(
                f"{DRIVERS}/{driver_id}/schedule",
                headers=headers,
                json=_weekly_put_body(active_dows=[0]),
            )
        assert put.status_code == 200

        row = await db_session.get(DriverShift, manual_id)
        assert row is not None
        assert row.origin == ShiftOrigin.MANUAL.value
        assert row.start_time.hour == 10


class TestShiftHolidayValidation:
    """create_shift respects allow_shifts / allowed_driver_ids."""

    @pytest.mark.asyncio
    async def test_create_shift_rejected_on_blocking_holiday(
        self, client: AsyncClient, user_factory
    ) -> None:
        driver_id, _, headers = await _create_internal_driver(client, user_factory)
        blocked_day = _next_weekday(2)

        hol = await client.post(
            HOLIDAYS + "/",
            headers=headers,
            json=_valid_create_payload(
                name="Blocked Day",
                start_date=str(blocked_day),
                end_date=str(blocked_day),
                allow_shifts=False,
            ),
        )
        assert hol.status_code == 201

        shift = await client.post(
            f"{DRIVERS}/shifts",
            headers=headers,
            data={
                "driver_id": driver_id,
                "date": str(blocked_day),
                "start_time": "08:00:00",
                "end_time": "16:00:00",
            },
        )
        assert shift.status_code == 422
        msg = shift.json().get("message", "").lower()
        assert "not allowed" in msg or "holiday" in msg

    @pytest.mark.asyncio
    async def test_create_shift_allowed_for_listed_driver_on_holiday(
        self, client: AsyncClient, user_factory
    ) -> None:
        driver_id, _, headers = await _create_internal_driver(client, user_factory)
        allowed_day = _next_weekday(3)

        hol = await client.post(
            HOLIDAYS + "/",
            headers=headers,
            json=_valid_create_payload(
                name="Allowed Day",
                start_date=str(allowed_day),
                end_date=str(allowed_day),
                allow_shifts=True,
                allowed_driver_ids=[driver_id],
            ),
        )
        assert hol.status_code == 201

        shift = await client.post(
            f"{DRIVERS}/shifts",
            headers=headers,
            data={
                "driver_id": driver_id,
                "date": str(allowed_day),
                "start_time": "08:00:00",
                "end_time": "16:00:00",
            },
        )
        assert shift.status_code == 201

    @pytest.mark.asyncio
    async def test_create_shift_rejected_when_allow_shifts_true_but_empty_list(
        self, client: AsyncClient, user_factory
    ) -> None:
        driver_id, _, headers = await _create_internal_driver(client, user_factory)
        blocked_day = _next_weekday(4)

        hol = await client.post(
            HOLIDAYS + "/",
            headers=headers,
            json=_valid_create_payload(
                name="Empty Allow List",
                start_date=str(blocked_day),
                end_date=str(blocked_day),
                allow_shifts=True,
                allowed_driver_ids=[],
            ),
        )
        assert hol.status_code == 201

        shift = await client.post(
            f"{DRIVERS}/shifts",
            headers=headers,
            data={
                "driver_id": driver_id,
                "date": str(blocked_day),
                "start_time": "08:00:00",
                "end_time": "16:00:00",
            },
        )
        assert shift.status_code == 422


class TestDriverWorkScheduleApi:
    """GET /v1/driver-profile/me/work-schedule day types with holidays + shifts."""

    @pytest.mark.asyncio
    async def test_work_schedule_holiday_vs_working_with_allow_shifts(
        self, client: AsyncClient, user_factory
    ) -> None:
        driver_id, driver_user_id, admin_headers = await _create_internal_driver(client, user_factory)
        driver_headers = {
            "Authorization": f"Bearer {create_access_token(user_id=driver_user_id, role='DRIVER', client_type='DRIVER')[0]}",
            "X-Client-Type": "DRIVER",
        }
        work_day = _next_weekday(1)

        await client.post(
            HOLIDAYS + "/",
            headers=admin_headers,
            json=_valid_create_payload(
                name="Company Holiday",
                start_date=str(work_day),
                end_date=str(work_day),
                allow_shifts=True,
                allowed_driver_ids=[driver_id],
            ),
        )

        await client.post(
            f"{DRIVERS}/shifts",
            headers=admin_headers,
            data={
                "driver_id": driver_id,
                "date": str(work_day),
                "start_time": "09:00:00",
                "end_time": "17:00:00",
            },
        )

        resp = await client.get(
            f"{DRIVER_PROFILE}/work-schedule",
            headers=driver_headers,
            params={
                "view": "weekly",
                "start_date": str(work_day),
                "end_date": str(work_day),
            },
        )
        assert resp.status_code == 200
        day = resp.json()["data"]["days"][0]
        assert day["day_type"] == "WORKING"
        assert day["shift_hours"] is not None
        assert day["holiday_name"] == "Company Holiday"

    @pytest.mark.asyncio
    async def test_work_schedule_shows_holiday_when_blocked(
        self, client: AsyncClient, user_factory
    ) -> None:
        driver_id, driver_user_id, admin_headers = await _create_internal_driver(client, user_factory)
        driver_headers = {
            "Authorization": f"Bearer {create_access_token(user_id=driver_user_id, role='DRIVER', client_type='DRIVER')[0]}",
            "X-Client-Type": "DRIVER",
        }
        hol_day = _next_weekday(5)

        await client.post(
            HOLIDAYS + "/",
            headers=admin_headers,
            json=_valid_create_payload(
                name="Public Holiday",
                start_date=str(hol_day),
                end_date=str(hol_day),
                allow_shifts=False,
            ),
        )

        resp = await client.get(
            f"{DRIVER_PROFILE}/work-schedule/day",
            headers=driver_headers,
            params={"date": str(hol_day)},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["day_type"] == "HOLIDAY"
        assert data["holiday_name"] == "Public Holiday"
        assert data.get("shift_hours") is None


class TestWorkScheduleServiceLayer:
    """Direct service-layer checks for read model and sync."""

    @pytest.mark.asyncio
    async def test_sync_skips_holiday_blocked_template_dates(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        driver_id, _, headers = await _create_internal_driver(client, user_factory)
        monday = _next_weekday(0)

        await client.post(
            HOLIDAYS + "/",
            headers=headers,
            json=_valid_create_payload(
                name="Monday Block",
                start_date=str(monday),
                end_date=str(monday),
                allow_shifts=False,
            ),
        )

        weekly_repo = DriverWeeklyScheduleRepository(db_session)
        await weekly_repo.create(
            {
                "driver_id": driver_id,
                "day_of_week": 0,
                "is_active": True,
                "start_time": time(9, 0),
                "end_time": time(17, 0),
            }
        )
        await db_session.flush()

        ctx = ScheduleContext(
            session=db_session,
            driver_repo=DriverRepository(db_session),
            shift_repo=DriverShiftRepository(db_session),
            weekly_repo=weekly_repo,
            user_repo=UserRepository(db_session),
        )
        changes = await sync_shifts_from_weekly_template(ctx, driver_id=driver_id, horizon_weeks=4)
        assert changes >= 0

        shift_on_monday = (
            await db_session.execute(
                select(DriverShift).where(
                    DriverShift.driver_id == driver_id,
                    DriverShift.shift_date == monday,
                )
            )
        ).scalar_one_or_none()
        assert shift_on_monday is None

    @pytest.mark.asyncio
    async def test_get_driver_work_schedule_allowed_holiday_without_shift(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        driver_id, _, headers = await _create_internal_driver(client, user_factory)
        hol_day = _next_weekday(6)

        await client.post(
            HOLIDAYS + "/",
            headers=headers,
            json=_valid_create_payload(
                name="Optional Work Holiday",
                start_date=str(hol_day),
                end_date=str(hol_day),
                allow_shifts=True,
                allowed_driver_ids=[driver_id],
            ),
        )

        ctx = ScheduleContext(
            session=db_session,
            driver_repo=DriverRepository(db_session),
            shift_repo=DriverShiftRepository(db_session),
            weekly_repo=DriverWeeklyScheduleRepository(db_session),
            user_repo=UserRepository(db_session),
        )
        days = await get_driver_work_schedule(
            ctx, driver_id=driver_id, from_date=hol_day, to_date=hol_day
        )
        assert len(days) == 1
        assert days[0]["day_type"] == "HOLIDAY"
        assert days[0]["holiday_name"] == "Optional Work Holiday"


class TestScheduleNotifications:
    @pytest.mark.asyncio
    async def test_weekly_put_enqueues_work_schedule_notification(
        self, client: AsyncClient, user_factory
    ) -> None:
        driver_id, _, headers = await _create_internal_driver(client, user_factory)
        with patch(NOTIFY_PATCH, new_callable=AsyncMock, return_value=True) as notify_mock:
            resp = await client.put(
                f"{DRIVERS}/{driver_id}/schedule",
                headers=headers,
                json=_weekly_put_body(active_dows=[0]),
            )
        assert resp.status_code == 200
        assert notify_mock.await_count >= 1
        call_kwargs = notify_mock.await_args_list[-1].kwargs
        from app.modules.notifications.enums import NotificationEvent

        assert call_kwargs["event"] == NotificationEvent.DRIVER_WORK_SCHEDULE_UPDATED

    @pytest.mark.asyncio
    async def test_holiday_allow_list_update_notifies_driver(
        self, client: AsyncClient, user_factory, db_session
    ) -> None:
        driver_id, _, headers = await _create_internal_driver(client, user_factory)
        create = await client.post(
            HOLIDAYS + "/",
            headers=headers,
            json=_valid_create_payload(
                name="Notify Holiday",
                start_date="2026-12-25",
                end_date="2026-12-25",
                allow_shifts=True,
                allowed_driver_ids=[],
            ),
        )
        holiday_id = create.json()["data"]["id"]

        with patch(NOTIFY_PATCH, new_callable=AsyncMock, return_value=True) as notify_mock:
            patch_resp = await client.patch(
                HOLIDAYS + f"/{holiday_id}",
                headers=headers,
                json={"allowed_driver_ids": [driver_id]},
            )
        assert patch_resp.status_code == 200
        assert notify_mock.await_count >= 1
