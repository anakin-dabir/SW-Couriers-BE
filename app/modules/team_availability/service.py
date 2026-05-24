"""Team availability — admin settings calendar and who's-off views."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.modules.drivers.enums import TimeOffType
from app.modules.drivers.service import DriverService
from app.modules.holidays.models import Holiday
from app.modules.team_availability.constants import (
    LEAVE_TYPE_DISPLAY,
    MAX_CALENDAR_DAY_ENTRIES,
    MAX_CALENDAR_RANGE_DAYS,
    MAX_WHO_IS_OFF_RANGE_DAYS,
    STAFF_LEAVE_ROLES,
)
from app.modules.team_availability.enums import LeavePaymentStatus, TeamMemberType
from app.modules.team_availability.models import StaffTimeOff
from app.modules.team_availability.repository import TeamAvailabilityRepository
from app.modules.user.models import User


def _validate_date_range(*, from_date: date, to_date: date, max_days: int) -> None:
    if to_date < from_date:
        raise ValidationError("from_date cannot be after to_date")
    if (to_date - from_date).days > max_days:
        raise ValidationError(f"Date range cannot exceed {max_days} days")


def _normalize_time_off_types(raw: list[str] | None) -> list[str] | None:
    if not raw:
        return None
    allowed = {t.value for t in TimeOffType}
    normalized: list[str] = []
    for item in raw:
        value = (item or "").strip().upper()
        if not value:
            continue
        if value not in allowed:
            raise ValidationError(f"Invalid time_off_type: {item}")
        normalized.append(value)
    return normalized or None


def _short_name(first_name: str | None, last_name: str | None) -> str:
    first = (first_name or "").strip()
    last = (last_name or "").strip()
    if not first and not last:
        return "Unknown"
    if not last:
        return first[:1].upper() + "." if len(first) > 1 else first
    initial = first[:1].upper() + "." if first else ""
    return f"{initial} {last}".strip()


def _display_name(first_name: str | None, last_name: str | None) -> str:
    return f"{(first_name or '').strip()} {(last_name or '').strip()}".strip() or "Unknown"


def _leave_type_meta(leave_type: str) -> tuple[str, str]:
    try:
        enum_val = TimeOffType(leave_type)
    except ValueError:
        return leave_type.replace("_", " ").title(), "#6B7280"
    meta = LEAVE_TYPE_DISPLAY.get(enum_val, {})
    return meta.get("label", enum_val.value.replace("_", " ").title()), meta.get("color_hex", "#6B7280")


def _duration_days(start: date, end: date, stored: int | None) -> int:
    if stored is not None and stored > 0:
        return stored
    return (end - start).days + 1


def _duration_label(days: int) -> str:
    return "Full Day" if days == 1 else f"{days} Days"


def _payment_status(is_paid: bool) -> LeavePaymentStatus:
    return LeavePaymentStatus.PAID if is_paid else LeavePaymentStatus.UNPAID


def _assert_staff_leave_eligible(role: str) -> None:
    if role not in STAFF_LEAVE_ROLES:
        raise ForbiddenError("My Leaves is only available for admin users.")


def _iter_days_in_window(*, start: date, end: date, window_from: date, window_to: date):
    cur = max(start, window_from)
    last = min(end, window_to)
    while cur <= last:
        yield cur
        cur += timedelta(days=1)


class TeamAvailabilityService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._repo = TeamAvailabilityRepository(session)
        self._driver_service = DriverService(session, request)

    async def list_leave_types(self) -> list[dict]:
        return [
            {
                "type": leave_type.value,
                "label": meta["label"],
                "color_hex": meta["color_hex"],
            }
            for leave_type, meta in LEAVE_TYPE_DISPLAY.items()
        ]

    async def get_team_calendar(
        self,
        *,
        from_date: date,
        to_date: date,
        time_off_type: list[str] | None = None,
        depot_id: str | None = None,
        driver_ids: list[str] | None = None,
        include_holidays: bool = True,
        only_my_leaves: bool = False,
        current_user_id: str | None = None,
    ) -> dict:
        _validate_date_range(from_date=from_date, to_date=to_date, max_days=MAX_CALENDAR_RANGE_DAYS)
        types = _normalize_time_off_types(time_off_type)
        only_user_id = current_user_id if only_my_leaves else None

        driver_leaves = await self._repo.list_driver_time_off_in_range(
            from_date=from_date,
            to_date=to_date,
            time_off_types=types,
            depot_id=depot_id,
            driver_ids=driver_ids,
            only_user_id=only_user_id,
        )
        staff_leaves = await self._repo.list_staff_time_off_in_range(
            from_date=from_date,
            to_date=to_date,
            time_off_types=types,
            only_user_id=only_user_id,
        )

        holidays: list[Holiday] = []
        if include_holidays and not only_my_leaves:
            holidays = await self._repo.list_holidays_in_range(from_date=from_date, to_date=to_date)

        leave_entries: list[dict] = []
        holiday_entries: list[dict] = []
        drivers_on_leave: set[str] = set()
        staff_on_leave: set[str] = set()
        entry_count = 0

        for leave in driver_leaves:
            driver = leave.driver
            user: User | None = driver.user if driver is not None else None
            label, color = _leave_type_meta(leave.type)
            short = _short_name(getattr(user, "first_name", None), getattr(user, "last_name", None))
            display = _display_name(getattr(user, "first_name", None), getattr(user, "last_name", None))
            is_current_user = bool(current_user_id and user and user.id == current_user_id)
            photo_url = self._driver_service.get_profile_photo_url(
                getattr(driver, "profile_photo_key", None) if driver else None
            )
            drivers_on_leave.add(leave.driver_id)

            for day in _iter_days_in_window(
                start=leave.start_date,
                end=leave.end_date,
                window_from=from_date,
                window_to=to_date,
            ):
                entry_count += 1
                if entry_count > MAX_CALENDAR_DAY_ENTRIES:
                    raise ValidationError(
                        "Too many calendar entries for this range. Narrow the date range, depot, or leave-type filters."
                    )
                leave_entries.append(
                    {
                        "id": leave.id,
                        "member_type": TeamMemberType.DRIVER.value,
                        "calendar_date": day,
                        "source": "TIME_OFF",
                        "driver_id": leave.driver_id,
                        "driver_code": getattr(driver, "driver_code", None) if driver else None,
                        "user_id": user.id if user else None,
                        "short_name": "You" if is_current_user else short,
                        "display_name": display,
                        "profile_photo_url": photo_url,
                        "time_off_type": leave.type,
                        "leave_type_label": label,
                        "color_hex": color,
                        "start_date": leave.start_date,
                        "end_date": leave.end_date,
                        "is_paid": leave.is_paid,
                        "is_current_user": is_current_user,
                    }
                )

        for leave in staff_leaves:
            user: User | None = leave.user
            label, color = _leave_type_meta(leave.type)
            short = _short_name(getattr(user, "first_name", None), getattr(user, "last_name", None))
            display = _display_name(getattr(user, "first_name", None), getattr(user, "last_name", None))
            is_current_user = bool(current_user_id and user and user.id == current_user_id)
            staff_on_leave.add(leave.user_id)

            for day in _iter_days_in_window(
                start=leave.start_date,
                end=leave.end_date,
                window_from=from_date,
                window_to=to_date,
            ):
                entry_count += 1
                if entry_count > MAX_CALENDAR_DAY_ENTRIES:
                    raise ValidationError(
                        "Too many calendar entries for this range. Narrow the date range, depot, or leave-type filters."
                    )
                leave_entries.append(
                    {
                        "id": leave.id,
                        "member_type": TeamMemberType.STAFF.value,
                        "calendar_date": day,
                        "source": "TIME_OFF",
                        "driver_id": None,
                        "driver_code": None,
                        "user_id": user.id if user else leave.user_id,
                        "short_name": "You" if is_current_user else short,
                        "display_name": display,
                        "profile_photo_url": None,
                        "time_off_type": leave.type,
                        "leave_type_label": label,
                        "color_hex": color,
                        "start_date": leave.start_date,
                        "end_date": leave.end_date,
                        "is_paid": leave.is_paid,
                        "is_current_user": is_current_user,
                    }
                )

        if include_holidays and not only_my_leaves:
            for holiday in holidays:
                for day in _iter_days_in_window(
                    start=holiday.start_date,
                    end=holiday.end_date,
                    window_from=from_date,
                    window_to=to_date,
                ):
                    entry_count += 1
                    if entry_count > MAX_CALENDAR_DAY_ENTRIES:
                        raise ValidationError(
                            "Too many calendar entries for this range. Narrow the date range or disable holidays."
                        )
                    holiday_entries.append(
                        {
                            "id": holiday.id,
                            "calendar_date": day,
                            "source": "HOLIDAY",
                            "holiday_name": holiday.name,
                            "start_date": holiday.start_date,
                            "end_date": holiday.end_date,
                            "audience": holiday.audience,
                        }
                    )

        leave_entries.sort(key=lambda e: (e["calendar_date"], e["display_name"]))
        holiday_entries.sort(key=lambda e: (e["calendar_date"], e["holiday_name"]))

        return {
            "from_date": from_date,
            "to_date": to_date,
            "summary": {
                "drivers_on_leave_count": len(drivers_on_leave),
                "staff_on_leave_count": len(staff_on_leave),
                "leave_day_entries_count": len(leave_entries),
                "holiday_day_entries_count": len(holiday_entries),
            },
            "leave_entries": leave_entries,
            "holiday_entries": holiday_entries,
        }

    async def list_who_is_off(
        self,
        *,
        from_date: date,
        to_date: date,
        time_off_type: list[str] | None = None,
        depot_id: str | None = None,
        driver_ids: list[str] | None = None,
        only_my_leaves: bool = False,
        current_user_id: str | None = None,
    ) -> dict:
        _validate_date_range(from_date=from_date, to_date=to_date, max_days=MAX_WHO_IS_OFF_RANGE_DAYS)
        types = _normalize_time_off_types(time_off_type)
        only_user_id = current_user_id if only_my_leaves else None

        driver_leaves = await self._repo.list_driver_time_off_in_range(
            from_date=from_date,
            to_date=to_date,
            time_off_types=types,
            depot_id=depot_id,
            driver_ids=driver_ids,
            only_user_id=only_user_id,
        )
        staff_leaves = await self._repo.list_staff_time_off_in_range(
            from_date=from_date,
            to_date=to_date,
            time_off_types=types,
            only_user_id=only_user_id,
        )

        items: list[dict] = []
        for leave in driver_leaves:
            driver = leave.driver
            user: User | None = driver.user if driver is not None else None
            label, color = _leave_type_meta(leave.type)
            display = _display_name(getattr(user, "first_name", None), getattr(user, "last_name", None))
            is_current_user = bool(current_user_id and user and user.id == current_user_id)
            items.append(
                {
                    "time_off_id": leave.id,
                    "member_type": TeamMemberType.DRIVER.value,
                    "driver_id": leave.driver_id,
                    "driver_code": getattr(driver, "driver_code", None) if driver else None,
                    "user_id": user.id if user else None,
                    "display_name": "You" if is_current_user else display,
                    "profile_photo_url": self._driver_service.get_profile_photo_url(
                        getattr(driver, "profile_photo_key", None) if driver else None
                    ),
                    "time_off_type": leave.type,
                    "leave_type_label": label,
                    "color_hex": color,
                    "start_date": leave.start_date,
                    "end_date": leave.end_date,
                    "duration_days": _duration_days(leave.start_date, leave.end_date, leave.days),
                    "is_current_user": is_current_user,
                }
            )

        for leave in staff_leaves:
            user = leave.user
            label, color = _leave_type_meta(leave.type)
            display = _display_name(getattr(user, "first_name", None), getattr(user, "last_name", None))
            is_current_user = bool(current_user_id and user and user.id == current_user_id)
            items.append(
                {
                    "time_off_id": leave.id,
                    "member_type": TeamMemberType.STAFF.value,
                    "driver_id": None,
                    "driver_code": None,
                    "user_id": user.id if user else leave.user_id,
                    "display_name": "You" if is_current_user else display,
                    "profile_photo_url": None,
                    "time_off_type": leave.type,
                    "leave_type_label": label,
                    "color_hex": color,
                    "start_date": leave.start_date,
                    "end_date": leave.end_date,
                    "duration_days": _duration_days(leave.start_date, leave.end_date, leave.days),
                    "is_current_user": is_current_user,
                }
            )

        items.sort(key=lambda row: (row["start_date"], row["display_name"]))
        return {
            "from_date": from_date,
            "to_date": to_date,
            "items": items,
            "total": len(items),
        }

    async def get_leave_detail(self, *, time_off_id: str, member_type: str) -> dict:
        if member_type == TeamMemberType.STAFF.value:
            return await self._staff_leave_detail(time_off_id)
        return await self._driver_leave_detail(time_off_id)

    async def _driver_leave_detail(self, time_off_id: str) -> dict:
        leave = await self._repo.get_driver_time_off_with_driver(time_off_id)
        if leave is None:
            raise NotFoundError(resource="driver_time_off", id=time_off_id)

        driver = leave.driver
        user: User | None = driver.user if driver is not None else None
        label, color = _leave_type_meta(leave.type)
        duration = _duration_days(leave.start_date, leave.end_date, leave.days)

        return {
            "id": leave.id,
            "member_type": TeamMemberType.DRIVER.value,
            "driver_id": leave.driver_id,
            "driver_code": getattr(driver, "driver_code", None) if driver else None,
            "user_id": user.id if user else None,
            "first_name": getattr(user, "first_name", None) if user else None,
            "last_name": getattr(user, "last_name", None) if user else None,
            "email": getattr(user, "email", None) if user else None,
            "profile_photo_url": self._driver_service.get_profile_photo_url(
                getattr(driver, "profile_photo_key", None) if driver else None
            ),
            "start_date": leave.start_date,
            "end_date": leave.end_date,
            "duration_days": duration,
            "duration_label": _duration_label(duration),
            "type": leave.type,
            "leave_type_label": label,
            "color_hex": color,
            "leave_status": _payment_status(leave.is_paid).value,
            "notes": leave.notes,
            "is_paid": leave.is_paid,
        }

    async def _staff_leave_detail(self, time_off_id: str) -> dict:
        leave = await self._repo.get_staff_time_off(time_off_id)
        if leave is None:
            raise NotFoundError(resource="staff_time_off", id=time_off_id)

        user = leave.user
        label, color = _leave_type_meta(leave.type)
        duration = _duration_days(leave.start_date, leave.end_date, leave.days)

        return {
            "id": leave.id,
            "member_type": TeamMemberType.STAFF.value,
            "driver_id": None,
            "driver_code": None,
            "user_id": user.id if user else leave.user_id,
            "first_name": getattr(user, "first_name", None) if user else None,
            "last_name": getattr(user, "last_name", None) if user else None,
            "email": getattr(user, "email", None) if user else None,
            "profile_photo_url": None,
            "start_date": leave.start_date,
            "end_date": leave.end_date,
            "duration_days": duration,
            "duration_label": _duration_label(duration),
            "type": leave.type,
            "leave_type_label": label,
            "color_hex": color,
            "leave_status": _payment_status(leave.is_paid).value,
            "notes": leave.notes,
            "is_paid": leave.is_paid,
        }

    # ── My Leaves (staff / admin) ─────────────────────────────────────────

    async def list_my_leaves(self, *, user_id: str, role: str) -> dict:
        _assert_staff_leave_eligible(role)
        items = await self._repo.list_staff_time_off_for_user(user_id)
        current_year = date.today().year
        paid_leave_taken = sum(
            (row.days or 0) for row in items if row.is_paid and row.start_date.year == current_year
        )
        unpaid_leave_taken = sum(
            (row.days or 0) for row in items if not row.is_paid and row.start_date.year == current_year
        )
        rows: list[dict] = []
        for leave in items:
            label, color = _leave_type_meta(leave.type)
            days = _duration_days(leave.start_date, leave.end_date, leave.days)
            rows.append(
                {
                    "id": leave.id,
                    "start_date": leave.start_date,
                    "end_date": leave.end_date,
                    "type": leave.type,
                    "leave_type_label": label,
                    "color_hex": color,
                    "days": days,
                    "duration_label": _duration_label(days),
                    "leave_status": _payment_status(leave.is_paid).value,
                    "is_paid": leave.is_paid,
                    "notes": leave.notes,
                    "can_edit": True,
                    "can_delete": True,
                }
            )
        return {
            "items": rows,
            "paid_leave_taken": paid_leave_taken,
            "unpaid_leave_taken": unpaid_leave_taken,
            "total": len(rows),
        }

    async def create_my_leave(
        self,
        *,
        user_id: str,
        role: str,
        start_date: date,
        end_date: date,
        leave_type: str,
        is_paid: bool,
        notes: str | None,
    ) -> dict:
        _assert_staff_leave_eligible(role)
        if end_date < start_date:
            raise ValidationError("end_date cannot be before start_date")
        types = _normalize_time_off_types([leave_type])
        assert types is not None
        if await self._repo.has_overlapping_staff_leave(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
        ):
            raise ValidationError("Leave dates overlap an existing entry.")

        days = (end_date - start_date).days + 1
        leave = await self._repo.create_staff_time_off(
            {
                "user_id": user_id,
                "start_date": start_date,
                "end_date": end_date,
                "type": types[0],
                "days": days,
                "notes": notes,
                "is_paid": is_paid,
            }
        )
        return self._staff_leave_to_item(leave)

    async def get_my_leave(self, *, user_id: str, role: str, time_off_id: str) -> dict:
        _assert_staff_leave_eligible(role)
        leave = await self._assert_staff_leave_owner(user_id=user_id, time_off_id=time_off_id)
        return self._staff_leave_to_item(leave)

    async def update_my_leave(
        self,
        *,
        user_id: str,
        role: str,
        time_off_id: str,
        start_date: date | None = None,
        end_date: date | None = None,
        leave_type: str | None = None,
        is_paid: bool | None = None,
        notes: str | None = None,
    ) -> dict:
        _assert_staff_leave_eligible(role)
        leave = await self._assert_staff_leave_owner(user_id=user_id, time_off_id=time_off_id)

        new_start = start_date or leave.start_date
        new_end = end_date or leave.end_date
        if new_end < new_start:
            raise ValidationError("end_date cannot be before start_date")

        if await self._repo.has_overlapping_staff_leave(
            user_id=user_id,
            start_date=new_start,
            end_date=new_end,
            exclude_id=time_off_id,
        ):
            raise ValidationError("Leave dates overlap an existing entry.")

        data: dict = {}
        if start_date is not None:
            data["start_date"] = start_date
        if end_date is not None:
            data["end_date"] = end_date
        if leave_type is not None:
            types = _normalize_time_off_types([leave_type])
            assert types is not None
            data["type"] = types[0]
        if is_paid is not None:
            data["is_paid"] = is_paid
        if notes is not None:
            data["notes"] = notes
        if start_date is not None or end_date is not None:
            data["days"] = (new_end - new_start).days + 1

        if not data:
            return self._staff_leave_to_item(leave)

        updated = await self._repo.update_staff_time_off(time_off_id, data)
        return self._staff_leave_to_item(updated)

    async def delete_my_leave(self, *, user_id: str, role: str, time_off_id: str) -> None:
        _assert_staff_leave_eligible(role)
        await self._assert_staff_leave_owner(user_id=user_id, time_off_id=time_off_id)
        await self._repo.delete_staff_time_off(time_off_id)

    async def _assert_staff_leave_owner(self, *, user_id: str, time_off_id: str) -> StaffTimeOff:
        leave = await self._repo.get_staff_time_off(time_off_id)
        if leave is None:
            raise NotFoundError(resource="staff_time_off", id=time_off_id)
        if leave.user_id != user_id:
            raise ForbiddenError("You can only manage your own leave requests.")
        return leave

    def _staff_leave_to_item(self, leave: StaffTimeOff) -> dict:
        label, color = _leave_type_meta(leave.type)
        days = _duration_days(leave.start_date, leave.end_date, leave.days)
        return {
            "id": leave.id,
            "start_date": leave.start_date,
            "end_date": leave.end_date,
            "type": leave.type,
            "leave_type_label": label,
            "color_hex": color,
            "days": days,
            "duration_label": _duration_label(days),
            "leave_status": _payment_status(leave.is_paid).value,
            "is_paid": leave.is_paid,
            "notes": leave.notes,
            "can_edit": True,
            "can_delete": True,
        }
