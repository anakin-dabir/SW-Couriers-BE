"""Sync weekly template rows into date-specific driver shifts."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select

from app.modules.drivers.enums import ShiftOrigin, ShiftStatus
from app.modules.drivers.models import DriverShift, DriverWeeklySchedule
from app.modules.drivers.schedule.constants import SYNC_HORIZON_WEEKS
from app.modules.drivers.schedule.context import ScheduleContext
from app.modules.drivers.schedule.holiday_rules import get_holiday_on_date, holiday_blocks_driver


async def sync_shifts_from_weekly_template(
    ctx: ScheduleContext,
    *,
    driver_id: str,
    horizon_weeks: int = SYNC_HORIZON_WEEKS,
) -> int:
    """Materialize weekly pattern into driver_shifts for [today, today + horizon].

    Only touches shifts with origin=WEEKLY_TEMPLATE. Skips dates with MANUAL shifts.
    Skips holiday-blocked dates (removes existing template shifts there).
    Returns count of rows created, updated, or deleted.
    """
    driver = await ctx.driver_repo.get_by_id_or_404(driver_id)
    today = date.today()
    end_date = today + timedelta(weeks=horizon_weeks)

    stmt = select(DriverWeeklySchedule).where(DriverWeeklySchedule.driver_id == driver_id)
    weekly_rows = list((await ctx.session.execute(stmt)).scalars().all())
    by_dow: dict[int, DriverWeeklySchedule] = {row.day_of_week: row for row in weekly_rows}

    shift_stmt = select(DriverShift).where(
        DriverShift.driver_id == driver_id,
        DriverShift.shift_date >= today,
        DriverShift.shift_date <= end_date,
    )
    existing_shifts = list((await ctx.session.execute(shift_stmt)).scalars().all())
    shifts_by_date = {s.shift_date: s for s in existing_shifts}

    changes = 0
    cur = today
    while cur <= end_date:
        dow = cur.weekday()
        template = by_dow.get(dow)
        existing = shifts_by_date.get(cur)

        if existing is not None and existing.origin == ShiftOrigin.MANUAL.value:
            cur += timedelta(days=1)
            continue

        is_active = (
            template is not None
            and template.is_active
            and template.start_time is not None
            and template.end_time is not None
        )

        holiday = await get_holiday_on_date(ctx.session, driver=driver, on_date=cur)
        blocked = holiday is not None and holiday_blocks_driver(driver.id, holiday)

        if blocked:
            if existing is not None and existing.origin == ShiftOrigin.WEEKLY_TEMPLATE.value:
                await ctx.shift_repo.hard_delete(existing.id)
                changes += 1
            cur += timedelta(days=1)
            continue

        if is_active:
            assert template is not None
            start_dt = datetime.combine(cur, template.start_time, tzinfo=UTC)
            end_dt = datetime.combine(cur, template.end_time, tzinfo=UTC)
            if existing is not None:
                if (
                    existing.origin == ShiftOrigin.WEEKLY_TEMPLATE.value
                    and (
                        existing.start_time != start_dt
                        or existing.end_time != end_dt
                        or existing.status != ShiftStatus.PLANNED.value
                    )
                ):
                    await ctx.shift_repo.update_by_id(
                        existing.id,
                        {
                            "start_time": start_dt,
                            "end_time": end_dt,
                            "status": ShiftStatus.PLANNED.value,
                            "origin": ShiftOrigin.WEEKLY_TEMPLATE.value,
                        },
                    )
                    changes += 1
            else:
                await ctx.shift_repo.create(
                    {
                        "driver_id": driver_id,
                        "shift_date": cur,
                        "start_time": start_dt,
                        "end_time": end_dt,
                        "status": ShiftStatus.PLANNED.value,
                        "origin": ShiftOrigin.WEEKLY_TEMPLATE.value,
                    }
                )
                changes += 1
        elif existing is not None and existing.origin == ShiftOrigin.WEEKLY_TEMPLATE.value:
            await ctx.shift_repo.hard_delete(existing.id)
            changes += 1

        cur += timedelta(days=1)

    return changes
