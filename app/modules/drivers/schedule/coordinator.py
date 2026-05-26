"""Coordinates schedule operations for DriverService."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from sqlalchemy import select

from app.common.exceptions import ValidationError
from app.modules.audit.service import AuditService
from app.modules.drivers.models import DriverShift
from app.modules.drivers.schedule.context import ScheduleContext
from app.modules.drivers.schedule.holiday_rules import assert_driver_may_work_on_date
from app.modules.drivers.schedule.notify import notify_driver_work_schedule_updated
from app.modules.drivers.schedule.sync import sync_shifts_from_weekly_template
from app.modules.drivers.schedule import work_schedule as work_schedule_mod


class DriverScheduleCoordinator:
    def __init__(self, ctx: ScheduleContext, audit: AuditService) -> None:
        self._ctx = ctx
        self._audit = audit

    async def get_driver_work_schedule(
        self, *, driver_id: str, from_date: date, to_date: date
    ) -> list[dict]:
        return await work_schedule_mod.get_driver_work_schedule(
            self._ctx, driver_id=driver_id, from_date=from_date, to_date=to_date
        )

    async def sync_after_weekly_change(
        self,
        *,
        driver_id: str,
        audit_user_id: str | None = None,
        change_summary: str = "Your weekly work schedule was updated",
    ) -> None:
        await sync_shifts_from_weekly_template(self._ctx, driver_id=driver_id)
        await notify_driver_work_schedule_updated(
            self._ctx.session,
            driver_id=driver_id,
            change_summary=change_summary,
            audit_user_id=audit_user_id,
        )

    async def notify_shift_change(
        self,
        *,
        driver_id: str,
        change_summary: str,
        effective_from: str | None = None,
        audit_user_id: str | None = None,
    ) -> None:
        await notify_driver_work_schedule_updated(
            self._ctx.session,
            driver_id=driver_id,
            change_summary=change_summary,
            effective_from=effective_from,
            audit_user_id=audit_user_id,
        )

    async def assert_driver_may_work(self, *, driver_id: str, on_date: date) -> None:
        driver = await self._ctx.driver_repo.get_by_id_or_404(driver_id)
        await assert_driver_may_work_on_date(self._ctx.session, driver=driver, on_date=on_date)

    async def _ensure_no_shift_conflict(
        self,
        *,
        driver_id: str,
        shift_date: date,
        start_time: time,
        end_time: time,
        exclude_shift_id: str | None = None,
    ) -> None:
        start_dt = datetime.combine(shift_date, start_time, tzinfo=UTC)
        end_dt = datetime.combine(shift_date, end_time, tzinfo=UTC)
        stmt = select(DriverShift).where(
            DriverShift.driver_id == driver_id,
            DriverShift.shift_date == shift_date,
            DriverShift.start_time < end_dt,
            DriverShift.end_time > start_dt,
        )
        if exclude_shift_id is not None:
            stmt = stmt.where(DriverShift.id != exclude_shift_id)
        result = await self._ctx.session.execute(stmt)
        existing = result.scalars().first()
        if existing:
            raise ValidationError("Shift overlaps with an existing shift for this driver")
