"""Unit tests for team availability service helpers and business rules."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.modules.drivers.enums import TimeOffType
from app.modules.team_availability import service as svc_module
from app.modules.team_availability.service import (
    TeamAvailabilityService,
    _display_name,
    _duration_days,
    _duration_label,
    _iter_days_in_window,
    _leave_type_meta,
    _normalize_time_off_types,
    _short_name,
    _validate_date_range,
)


class TestHelperFunctions:
    def test_validate_date_range_rejects_inverted(self) -> None:
        with pytest.raises(ValidationError, match="from_date cannot be after to_date"):
            _validate_date_range(from_date=date(2026, 4, 10), to_date=date(2026, 4, 1), max_days=93)

    def test_validate_date_range_rejects_too_long(self) -> None:
        start = date(2026, 1, 1)
        with pytest.raises(ValidationError, match="cannot exceed 10"):
            _validate_date_range(from_date=start, to_date=start + timedelta(days=11), max_days=10)

    def test_validate_date_range_allows_exact_max(self) -> None:
        start = date(2026, 1, 1)
        _validate_date_range(from_date=start, to_date=start + timedelta(days=10), max_days=10)

    def test_normalize_time_off_types_valid(self) -> None:
        assert _normalize_time_off_types(["sick_leave", " ANNUAL_LEAVE "]) == [
            "SICK_LEAVE",
            "ANNUAL_LEAVE",
        ]

    def test_normalize_time_off_types_empty_returns_none(self) -> None:
        assert _normalize_time_off_types(None) is None
        assert _normalize_time_off_types([]) is None
        assert _normalize_time_off_types(["", "  "]) is None

    def test_normalize_time_off_types_invalid_raises(self) -> None:
        with pytest.raises(ValidationError, match="Invalid time_off_type"):
            _normalize_time_off_types(["NOT_A_REAL_TYPE"])

    def test_short_name_formats(self) -> None:
        assert _short_name("Martin", "Butler") == "M. Butler"
        assert _short_name("Madonna", None) == "M."
        assert _short_name(None, "Lee") == "Lee"
        assert _short_name(None, None) == "Unknown"

    def test_display_name(self) -> None:
        assert _display_name("Sara", "Okafor") == "Sara Okafor"
        assert _display_name("", "") == "Unknown"

    def test_leave_type_meta_known_and_unknown(self) -> None:
        label, color = _leave_type_meta(TimeOffType.SICK_LEAVE.value)
        assert label == "Sick Leave"
        assert color == "#DC2626"
        label2, _ = _leave_type_meta("LEGACY_TYPE")
        assert label2 == "Legacy Type"

    def test_duration_days_prefers_stored(self) -> None:
        assert _duration_days(date(2026, 4, 1), date(2026, 4, 10), 3) == 3
        assert _duration_days(date(2026, 4, 1), date(2026, 4, 3), None) == 3

    def test_iter_days_in_window_clips_to_bounds(self) -> None:
        days = list(
            _iter_days_in_window(
                start=date(2026, 4, 1),
                end=date(2026, 4, 10),
                window_from=date(2026, 4, 5),
                window_to=date(2026, 4, 7),
            )
        )
        assert days == [date(2026, 4, 5), date(2026, 4, 6), date(2026, 4, 7)]


def _make_leave(
    *,
    leave_id: str = "to-1",
    driver_id: str = "drv-1",
    start: date | None = None,
    end: date | None = None,
    leave_type: str = "ANNUAL_LEAVE",
    days: int | None = 3,
    first_name: str = "Jane",
    last_name: str = "Driver",
    user_id: str = "user-1",
) -> SimpleNamespace:
    start = start or date(2026, 4, 1)
    end = end or date(2026, 4, 3)
    user = SimpleNamespace(id=user_id, first_name=first_name, last_name=last_name, email="j@example.com")
    driver = SimpleNamespace(
        id=driver_id,
        driver_code="DR-001",
        profile_photo_key=None,
        user=user,
    )
    return SimpleNamespace(
        id=leave_id,
        driver_id=driver_id,
        start_date=start,
        end_date=end,
        type=leave_type,
        days=days,
        notes="note",
        is_paid=True,
        driver=driver,
    )


def _make_staff_leave(
    *,
    leave_id: str = "staff-to-1",
    user_id: str = "user-admin-1",
    start: date | None = None,
    end: date | None = None,
    leave_type: str = "ANNUAL_LEAVE",
    days: int | None = 1,
    is_paid: bool = True,
    notes: str | None = "staff note",
    first_name: str = "Admin",
    last_name: str = "User",
) -> SimpleNamespace:
    start = start or date(2026, 5, 1)
    end = end or date(2026, 5, 1)
    user = SimpleNamespace(id=user_id, first_name=first_name, last_name=last_name, email="a@example.com", role="ADMIN")
    return SimpleNamespace(
        id=leave_id,
        user_id=user_id,
        start_date=start,
        end_date=end,
        type=leave_type,
        days=days,
        notes=notes,
        is_paid=is_paid,
        user=user,
    )


def _service_with_repo(
    *,
    driver_leaves: list | None = None,
    staff_leaves: list | None = None,
    holidays: list | None = None,
    driver_time_off_row=None,
    staff_time_off_row=None,
) -> TeamAvailabilityService:
    session = MagicMock()
    service = TeamAvailabilityService(session=session, request=None)
    service._repo = SimpleNamespace(
        list_driver_time_off_in_range=AsyncMock(return_value=driver_leaves or []),
        list_staff_time_off_in_range=AsyncMock(return_value=staff_leaves or []),
        list_holidays_in_range=AsyncMock(return_value=holidays or []),
        get_driver_time_off_with_driver=AsyncMock(return_value=driver_time_off_row),
        get_staff_time_off=AsyncMock(return_value=staff_time_off_row),
        list_staff_time_off_for_user=AsyncMock(return_value=[]),
        has_overlapping_staff_leave=AsyncMock(return_value=False),
        create_staff_time_off=AsyncMock(),
        update_staff_time_off=AsyncMock(),
        delete_staff_time_off=AsyncMock(),
    )
    service._driver_service = SimpleNamespace(get_profile_photo_url=MagicMock(return_value=None))
    return service


class TestTeamAvailabilityServiceUnit:
    @pytest.mark.asyncio
    async def test_list_leave_types_covers_all_enums(self) -> None:
        service = _service_with_repo()
        items = await service.list_leave_types()
        types = {row["type"] for row in items}
        assert types == {t.value for t in TimeOffType}

    @pytest.mark.asyncio
    async def test_get_team_calendar_expands_multi_day_leave(self) -> None:
        leave = _make_leave(start=date(2026, 4, 1), end=date(2026, 4, 3))
        service = _service_with_repo(driver_leaves=[leave])
        result = await service.get_team_calendar(
            from_date=date(2026, 4, 1),
            to_date=date(2026, 4, 30),
        )
        assert result["summary"]["drivers_on_leave_count"] == 1
        assert result["summary"]["leave_day_entries_count"] == 3
        dates = {e["calendar_date"] for e in result["leave_entries"]}
        assert dates == {date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)}

    @pytest.mark.asyncio
    async def test_get_team_calendar_clips_leave_outside_window(self) -> None:
        leave = _make_leave(start=date(2026, 3, 28), end=date(2026, 4, 5))
        service = _service_with_repo(driver_leaves=[leave])
        result = await service.get_team_calendar(
            from_date=date(2026, 4, 1),
            to_date=date(2026, 4, 30),
        )
        dates = sorted(e["calendar_date"] for e in result["leave_entries"])
        assert dates[0] == date(2026, 4, 1)
        assert dates[-1] == date(2026, 4, 5)
        assert date(2026, 3, 28) not in dates

    @pytest.mark.asyncio
    async def test_get_team_calendar_include_holidays_false(self) -> None:
        leave = _make_leave()
        holiday = SimpleNamespace(
            id="h-1",
            name="Easter",
            start_date=date(2026, 4, 5),
            end_date=date(2026, 4, 5),
            audience="BOTH",
        )
        service = _service_with_repo(driver_leaves=[leave], holidays=[holiday])
        result = await service.get_team_calendar(
            from_date=date(2026, 4, 1),
            to_date=date(2026, 4, 30),
            include_holidays=False,
        )
        assert result["holiday_entries"] == []
        service._repo.list_holidays_in_range.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_team_calendar_only_my_leaves_sets_user_filter(self) -> None:
        service = _service_with_repo()
        await service.get_team_calendar(
            from_date=date(2026, 4, 1),
            to_date=date(2026, 4, 7),
            only_my_leaves=True,
            current_user_id="user-99",
        )
        driver_kwargs = service._repo.list_driver_time_off_in_range.await_args.kwargs
        staff_kwargs = service._repo.list_staff_time_off_in_range.await_args.kwargs
        assert driver_kwargs["only_user_id"] == "user-99"
        assert staff_kwargs["only_user_id"] == "user-99"

    @pytest.mark.asyncio
    async def test_get_team_calendar_marks_current_user_as_you(self) -> None:
        leave = _make_leave(user_id="user-1")
        service = _service_with_repo(driver_leaves=[leave])
        result = await service.get_team_calendar(
            from_date=date(2026, 4, 1),
            to_date=date(2026, 4, 7),
            current_user_id="user-1",
        )
        assert result["leave_entries"][0]["short_name"] == "You"
        assert result["leave_entries"][0]["is_current_user"] is True

    @pytest.mark.asyncio
    async def test_get_team_calendar_entry_cap_raises(self) -> None:
        leave = _make_leave(start=date(2026, 4, 1), end=date(2026, 4, 1))
        service = _service_with_repo(driver_leaves=[leave])
        with patch.object(svc_module, "MAX_CALENDAR_DAY_ENTRIES", 0):
            with pytest.raises(ValidationError, match="Too many calendar entries"):
                await service.get_team_calendar(
                    from_date=date(2026, 4, 1),
                    to_date=date(2026, 4, 1),
                )

    @pytest.mark.asyncio
    async def test_list_who_is_off_range_limit(self) -> None:
        service = _service_with_repo()
        with pytest.raises(ValidationError, match="cannot exceed 14"):
            await service.list_who_is_off(
                from_date=date(2026, 4, 1),
                to_date=date(2026, 4, 20),
            )

    @pytest.mark.asyncio
    async def test_list_who_is_off_sorts_by_start_and_name(self) -> None:
        leave_a = _make_leave(leave_id="a", first_name="Zara", last_name="A", start=date(2026, 4, 5))
        leave_b = _make_leave(leave_id="b", first_name="Amy", last_name="B", start=date(2026, 4, 1))
        service = _service_with_repo(driver_leaves=[leave_a, leave_b])
        result = await service.list_who_is_off(
            from_date=date(2026, 4, 1),
            to_date=date(2026, 4, 10),
        )
        assert result["total"] == 2
        assert result["items"][0]["display_name"] == "Amy B"
        assert result["items"][1]["display_name"] == "Zara A"

    @pytest.mark.asyncio
    async def test_get_leave_detail_not_found(self) -> None:
        service = _service_with_repo(driver_time_off_row=None)
        with pytest.raises(NotFoundError):
            await service.get_leave_detail(time_off_id="missing-id", member_type="DRIVER")

    @pytest.mark.asyncio
    async def test_get_leave_detail_returns_email_and_duration(self) -> None:
        leave = _make_leave(days=5)
        service = _service_with_repo(driver_time_off_row=leave)
        detail = await service.get_leave_detail(time_off_id=leave.id, member_type="DRIVER")
        assert detail["email"] == "j@example.com"
        assert detail["duration_days"] == 5
        assert detail["duration_label"] == "5 Days"
        assert detail["leave_type_label"] == "Annual Leave"
        assert detail["leave_status"] == "PAID"

    @pytest.mark.asyncio
    async def test_get_leave_detail_staff_not_found(self) -> None:
        service = _service_with_repo(staff_time_off_row=None)
        with pytest.raises(NotFoundError):
            await service.get_leave_detail(time_off_id="missing-staff", member_type="STAFF")

    @pytest.mark.asyncio
    async def test_list_who_is_off_includes_staff(self) -> None:
        driver_leave = _make_leave(leave_id="drv-1", start=date(2026, 5, 1), end=date(2026, 5, 1))
        staff_leave = _make_staff_leave(leave_id="staff-1", start=date(2026, 5, 2), end=date(2026, 5, 3), days=2)
        service = _service_with_repo(driver_leaves=[driver_leave], staff_leaves=[staff_leave])
        result = await service.list_who_is_off(
            from_date=date(2026, 5, 1),
            to_date=date(2026, 5, 5),
        )
        assert result["total"] == 2
        types = {i["member_type"] for i in result["items"]}
        assert types == {"DRIVER", "STAFF"}

    @pytest.mark.asyncio
    async def test_get_team_calendar_counts_staff_on_leave(self) -> None:
        staff_leave = _make_staff_leave(start=date(2026, 5, 1), end=date(2026, 5, 1))
        service = _service_with_repo(staff_leaves=[staff_leave])
        result = await service.get_team_calendar(
            from_date=date(2026, 5, 1),
            to_date=date(2026, 5, 7),
            include_holidays=False,
        )
        assert result["summary"]["staff_on_leave_count"] == 1
        assert result["leave_entries"][0]["member_type"] == "STAFF"


class TestMyLeavesServiceUnit:
    @pytest.mark.asyncio
    async def test_create_my_leave_driver_role_forbidden(self) -> None:
        service = _service_with_repo()
        with pytest.raises(ForbiddenError, match="admin users"):
            await service.create_my_leave(
                user_id="u1",
                role="DRIVER",
                start_date=date(2026, 5, 1),
                end_date=date(2026, 5, 1),
                leave_type="SICK_LEAVE",
                is_paid=True,
                notes=None,
            )

    @pytest.mark.asyncio
    async def test_create_my_leave_rejects_inverted_dates(self) -> None:
        service = _service_with_repo()
        with pytest.raises(ValidationError, match="end_date cannot be before start_date"):
            await service.create_my_leave(
                user_id="u1",
                role="ADMIN",
                start_date=date(2026, 5, 10),
                end_date=date(2026, 5, 1),
                leave_type="SICK_LEAVE",
                is_paid=True,
                notes=None,
            )

    @pytest.mark.asyncio
    async def test_create_my_leave_overlap_raises(self) -> None:
        service = _service_with_repo()
        service._repo.has_overlapping_staff_leave = AsyncMock(return_value=True)
        with pytest.raises(ValidationError, match="overlap"):
            await service.create_my_leave(
                user_id="u1",
                role="ADMIN",
                start_date=date(2026, 5, 1),
                end_date=date(2026, 5, 1),
                leave_type="SICK_LEAVE",
                is_paid=True,
                notes=None,
            )

    @pytest.mark.asyncio
    async def test_create_my_leave_full_day_label(self) -> None:
        created = _make_staff_leave(leave_id="new-1", days=1)
        service = _service_with_repo()
        service._repo.create_staff_time_off = AsyncMock(return_value=created)
        item = await service.create_my_leave(
            user_id="user-admin-1",
            role="ADMIN",
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 1),
            leave_type="ANNUAL_LEAVE",
            is_paid=True,
            notes=None,
        )
        assert item["duration_label"] == "Full Day"
        assert item["leave_status"] == "PAID"

    @pytest.mark.asyncio
    async def test_list_my_leaves_paid_unpaid_totals(self) -> None:
        paid = _make_staff_leave(
            leave_id="p1",
            start=date(date.today().year, 3, 1),
            end=date(date.today().year, 3, 3),
            days=3,
            is_paid=True,
        )
        unpaid = _make_staff_leave(
            leave_id="u1",
            start=date(date.today().year, 4, 1),
            end=date(date.today().year, 4, 1),
            days=1,
            is_paid=False,
        )
        service = _service_with_repo()
        service._repo.list_staff_time_off_for_user = AsyncMock(return_value=[paid, unpaid])
        result = await service.list_my_leaves(user_id="user-admin-1", role="ADMIN")
        assert result["paid_leave_taken"] == 3
        assert result["unpaid_leave_taken"] == 1
        assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_get_my_leave_wrong_owner_forbidden(self) -> None:
        leave = _make_staff_leave(user_id="owner-1")
        service = _service_with_repo()
        service._repo.get_staff_time_off = AsyncMock(return_value=leave)
        with pytest.raises(ForbiddenError, match="your own"):
            await service.get_my_leave(user_id="other-user", role="ADMIN", time_off_id=leave.id)

    @pytest.mark.asyncio
    async def test_update_my_leave_overlap_on_date_change(self) -> None:
        leave = _make_staff_leave()
        service = _service_with_repo()
        service._repo.get_staff_time_off = AsyncMock(return_value=leave)
        service._repo.has_overlapping_staff_leave = AsyncMock(return_value=True)
        with pytest.raises(ValidationError, match="overlap"):
            await service.update_my_leave(
                user_id="user-admin-1",
                role="ADMIN",
                time_off_id=leave.id,
                end_date=date(2026, 5, 10),
            )

    @pytest.mark.asyncio
    async def test_delete_my_leave_calls_repo(self) -> None:
        leave = _make_staff_leave()
        service = _service_with_repo()
        service._repo.get_staff_time_off = AsyncMock(return_value=leave)
        await service.delete_my_leave(user_id="user-admin-1", role="ADMIN", time_off_id=leave.id)
        service._repo.delete_staff_time_off.assert_awaited_once_with(leave.id)


class TestDurationLabelHelper:
    def test_duration_label_single_day(self) -> None:
        assert _duration_label(1) == "Full Day"

    def test_duration_label_multi_day(self) -> None:
        assert _duration_label(10) == "10 Days"
