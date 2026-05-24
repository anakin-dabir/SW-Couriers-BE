"""Data access for team availability (driver + staff leave, holidays)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.common.repository import BaseRepository
from app.modules.drivers.enums import DriverAccountStatus
from app.modules.drivers.models import Driver, DriverTimeOff
from app.modules.holidays.models import Holiday
from app.modules.team_availability.constants import STAFF_LEAVE_ROLES
from app.modules.team_availability.models import StaffTimeOff
from app.modules.user.models import User


class StaffTimeOffRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, StaffTimeOff)


class TeamAvailabilityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._staff_time_off = StaffTimeOffRepository(session)

    # ── Driver time off (fleet) ───────────────────────────────────────────

    def _driver_time_off_base(
        self,
        *,
        from_date: date,
        to_date: date,
        time_off_types: list[str] | None,
        depot_id: str | None,
        driver_ids: list[str] | None,
        only_user_id: str | None,
    ) -> Select:
        stmt = (
            select(DriverTimeOff)
            .join(Driver, DriverTimeOff.driver_id == Driver.id)
            .join(User, Driver.user_id == User.id)
            .options(joinedload(DriverTimeOff.driver).joinedload(Driver.user))
            .where(
                Driver.user_id.is_not(None),
                Driver.account_status != DriverAccountStatus.DRAFT.value,
                DriverTimeOff.start_date <= to_date,
                DriverTimeOff.end_date >= from_date,
            )
        )
        if time_off_types:
            stmt = stmt.where(DriverTimeOff.type.in_(time_off_types))
        if depot_id is not None:
            stmt = stmt.where(Driver.depot_id == depot_id)
        if driver_ids:
            stmt = stmt.where(Driver.id.in_(driver_ids))
        if only_user_id is not None:
            stmt = stmt.where(User.id == only_user_id)
        return stmt.order_by(DriverTimeOff.start_date.asc(), Driver.id.asc())

    async def list_driver_time_off_in_range(
        self,
        *,
        from_date: date,
        to_date: date,
        time_off_types: list[str] | None = None,
        depot_id: str | None = None,
        driver_ids: list[str] | None = None,
        only_user_id: str | None = None,
    ) -> list[DriverTimeOff]:
        stmt = self._driver_time_off_base(
            from_date=from_date,
            to_date=to_date,
            time_off_types=time_off_types,
            depot_id=depot_id,
            driver_ids=driver_ids,
            only_user_id=only_user_id,
        )
        return list((await self._session.execute(stmt)).scalars().unique().all())

    async def get_driver_time_off_with_driver(self, time_off_id: str) -> DriverTimeOff | None:
        stmt = (
            select(DriverTimeOff)
            .where(DriverTimeOff.id == time_off_id)
            .options(joinedload(DriverTimeOff.driver).joinedload(Driver.user))
        )
        return (await self._session.execute(stmt)).unique().scalar_one_or_none()

    # ── Staff time off (admin My Leaves) ──────────────────────────────────

    def _staff_time_off_base(
        self,
        *,
        from_date: date,
        to_date: date,
        time_off_types: list[str] | None,
        only_user_id: str | None,
    ) -> Select:
        stmt = (
            select(StaffTimeOff)
            .join(User, StaffTimeOff.user_id == User.id)
            .options(joinedload(StaffTimeOff.user))
            .where(
                StaffTimeOff.start_date <= to_date,
                StaffTimeOff.end_date >= from_date,
                User.role.in_(tuple(STAFF_LEAVE_ROLES)),
            )
        )
        if time_off_types:
            stmt = stmt.where(StaffTimeOff.type.in_(time_off_types))
        if only_user_id is not None:
            stmt = stmt.where(StaffTimeOff.user_id == only_user_id)
        return stmt.order_by(StaffTimeOff.start_date.desc(), StaffTimeOff.id.asc())

    async def list_staff_time_off_in_range(
        self,
        *,
        from_date: date,
        to_date: date,
        time_off_types: list[str] | None = None,
        only_user_id: str | None = None,
    ) -> list[StaffTimeOff]:
        stmt = self._staff_time_off_base(
            from_date=from_date,
            to_date=to_date,
            time_off_types=time_off_types,
            only_user_id=only_user_id,
        )
        return list((await self._session.execute(stmt)).scalars().unique().all())

    async def list_staff_time_off_for_user(self, user_id: str) -> list[StaffTimeOff]:
        stmt = (
            select(StaffTimeOff)
            .where(StaffTimeOff.user_id == user_id)
            .order_by(StaffTimeOff.start_date.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_staff_time_off(self, time_off_id: str) -> StaffTimeOff | None:
        stmt = (
            select(StaffTimeOff)
            .where(StaffTimeOff.id == time_off_id)
            .options(joinedload(StaffTimeOff.user))
        )
        return (await self._session.execute(stmt)).unique().scalar_one_or_none()

    async def has_overlapping_staff_leave(
        self,
        *,
        user_id: str,
        start_date: date,
        end_date: date,
        exclude_id: str | None = None,
    ) -> bool:
        stmt = select(func.count()).select_from(StaffTimeOff).where(
            StaffTimeOff.user_id == user_id,
            StaffTimeOff.start_date <= end_date,
            StaffTimeOff.end_date >= start_date,
        )
        if exclude_id is not None:
            stmt = stmt.where(StaffTimeOff.id != exclude_id)
        count = (await self._session.execute(stmt)).scalar_one()
        return int(count) > 0

    async def create_staff_time_off(self, data: dict) -> StaffTimeOff:
        return await self._staff_time_off.create(data)

    async def update_staff_time_off(self, time_off_id: str, data: dict) -> StaffTimeOff:
        return await self._staff_time_off.update_by_id(time_off_id, data)

    async def delete_staff_time_off(self, time_off_id: str) -> None:
        await self._staff_time_off.hard_delete(time_off_id)

    # ── Holidays ──────────────────────────────────────────────────────────

    async def list_holidays_in_range(self, *, from_date: date, to_date: date) -> list[Holiday]:
        stmt = (
            select(Holiday)
            .where(
                Holiday.start_date <= to_date,
                Holiday.end_date >= from_date,
            )
            .order_by(Holiday.start_date.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())
