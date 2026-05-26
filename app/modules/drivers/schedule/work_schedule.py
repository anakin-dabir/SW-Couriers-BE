"""Mobile work-schedule read model (shifts + time-off + holidays + routes)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from app.modules.drivers.models import Driver, DriverShift, DriverTimeOff
from app.modules.drivers.schedule.context import ScheduleContext
from app.modules.drivers.schedule.holiday_rules import (
    expand_holidays_by_date,
    fetch_holidays_for_driver_in_range,
    holiday_blocks_driver,
)
from app.modules.planning.models import Route, RoutePlan
from app.modules.vehicles.models import Vehicle


async def get_driver_work_schedule(
    ctx: ScheduleContext,
    *,
    driver_id: str,
    from_date: date,
    to_date: date,
) -> list[dict]:
    """Return one entry per calendar day in [from_date, to_date].

    Priority: TIME_OFF > blocking HOLIDAY > (allowed holiday + shift → WORKING) > REST.
    Allowed holiday without shift still shows HOLIDAY. WORKING on allowed holiday includes holiday_name.
    """
    driver = await ctx.driver_repo.get_by_id_or_404(driver_id)
    sess = ctx.session

    shift_rows = await _list_shifts(ctx, driver_id=driver_id, date_from=from_date, date_to=to_date)
    shifts_by_date: dict[date, DriverShift] = {s.shift_date: s for s in shift_rows}

    time_off_stmt = (
        select(DriverTimeOff)
        .where(
            DriverTimeOff.driver_id == driver_id,
            DriverTimeOff.start_date <= to_date,
            DriverTimeOff.end_date >= from_date,
        )
        .order_by(DriverTimeOff.start_date)
    )
    time_off_rows = list((await sess.execute(time_off_stmt)).scalars().all())
    time_off_by_date: dict[date, DriverTimeOff] = {}
    for to_row in time_off_rows:
        cur = to_row.start_date
        while cur <= to_row.end_date:
            if from_date <= cur <= to_date and cur not in time_off_by_date:
                time_off_by_date[cur] = to_row
            cur += timedelta(days=1)

    holiday_rows = await fetch_holidays_for_driver_in_range(
        sess, driver=driver, from_date=from_date, to_date=to_date
    )
    holidays_by_date = expand_holidays_by_date(holiday_rows, from_date=from_date, to_date=to_date)

    route_stmt = (
        select(Route, RoutePlan.service_date, Vehicle.registration_number)
        .join(RoutePlan, Route.plan_id == RoutePlan.id)
        .outerjoin(Vehicle, Route.vehicle_id == Vehicle.id)
        .where(
            Route.driver_id == driver_id,
            RoutePlan.service_date >= from_date,
            RoutePlan.service_date <= to_date,
        )
        .order_by(RoutePlan.service_date)
    )
    route_result = (await sess.execute(route_stmt)).all()
    routes_by_date: dict[date, dict] = {}
    for route, svc_date, reg in route_result:
        if svc_date not in routes_by_date:
            routes_by_date[svc_date] = {
                "route_id": route.id,
                "route_code": route.route_code,
                "route_status": route.status,
                "vehicle_registration": reg,
            }

    days: list[dict] = []
    cur_day = from_date
    while cur_day <= to_date:
        entry: dict = {"date": cur_day, "route": routes_by_date.get(cur_day)}
        holiday = holidays_by_date.get(cur_day)

        if cur_day in time_off_by_date:
            to_row = time_off_by_date[cur_day]
            entry["day_type"] = "TIME_OFF"
            entry["time_off_type"] = to_row.type
            entry["time_off_is_paid"] = to_row.is_paid
        elif holiday is not None and holiday_blocks_driver(driver.id, holiday):
            entry["day_type"] = "HOLIDAY"
            entry["holiday_name"] = holiday.name
        elif cur_day in shifts_by_date:
            shift = shifts_by_date[cur_day]
            entry["day_type"] = "WORKING"
            entry["shift_hours"] = (
                f"{shift.start_time.strftime('%H:%M')} - {shift.end_time.strftime('%H:%M')}"
            )
            entry["shift_status"] = shift.status
            if holiday is not None:
                entry["holiday_name"] = holiday.name
        elif holiday is not None:
            entry["day_type"] = "HOLIDAY"
            entry["holiday_name"] = holiday.name
        else:
            entry["day_type"] = "REST"

        days.append(entry)
        cur_day += timedelta(days=1)

    return days


async def _list_shifts(
    ctx: ScheduleContext,
    *,
    driver_id: str,
    date_from: date,
    date_to: date,
) -> list[DriverShift]:
    stmt = (
        select(DriverShift)
        .where(
            DriverShift.driver_id == driver_id,
            DriverShift.shift_date >= date_from,
            DriverShift.shift_date <= date_to,
        )
        .order_by(DriverShift.shift_date, DriverShift.start_time)
    )
    result = await ctx.session.execute(stmt)
    return list(result.scalars().all())
