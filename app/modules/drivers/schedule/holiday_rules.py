"""Holiday allow_shifts / allowed-driver rules for driver scheduling."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.exceptions import ValidationError
from app.modules.drivers.models import Driver
from app.modules.holidays.enums import HolidayAudience
from app.modules.holidays.models import Holiday


def allowed_driver_ids(holiday: Holiday) -> set[str]:
    return {row.driver_id for row in holiday.allowed_drivers}


def is_driver_allowed_on_holiday(driver_id: str, holiday: Holiday) -> bool:
    if not holiday.allow_shifts:
        return False
    return driver_id in allowed_driver_ids(holiday)


def holiday_blocks_driver(driver_id: str, holiday: Holiday) -> bool:
    return not is_driver_allowed_on_holiday(driver_id, holiday)


def audience_values_for_driver(driver: Driver) -> list[str]:
    values = [HolidayAudience.BOTH.value]
    if driver.driver_type:
        values.append(str(driver.driver_type).upper())
    return values


async def fetch_holidays_for_driver_in_range(
    session: AsyncSession,
    *,
    driver: Driver,
    from_date: date,
    to_date: date,
) -> list[Holiday]:
    audience_allowed = audience_values_for_driver(driver)
    stmt = (
        select(Holiday)
        .where(
            Holiday.start_date <= to_date,
            Holiday.end_date >= from_date,
            Holiday.audience.in_(audience_allowed),
        )
        .options(selectinload(Holiday.allowed_drivers))
        .order_by(Holiday.start_date)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def expand_holidays_by_date(
    holidays: list[Holiday],
    *,
    from_date: date,
    to_date: date,
) -> dict[date, Holiday]:
    by_date: dict[date, Holiday] = {}
    for holiday in holidays:
        cur = holiday.start_date
        while cur <= holiday.end_date:
            if from_date <= cur <= to_date and cur not in by_date:
                by_date[cur] = holiday
            cur += timedelta(days=1)
    return by_date


async def get_holiday_on_date(
    session: AsyncSession,
    *,
    driver: Driver,
    on_date: date,
) -> Holiday | None:
    holidays = await fetch_holidays_for_driver_in_range(
        session,
        driver=driver,
        from_date=on_date,
        to_date=on_date,
    )
    by_date = expand_holidays_by_date(holidays, from_date=on_date, to_date=on_date)
    return by_date.get(on_date)


async def assert_driver_may_work_on_date(
    session: AsyncSession,
    *,
    driver: Driver,
    on_date: date,
) -> None:
    holiday = await get_holiday_on_date(session, driver=driver, on_date=on_date)
    if holiday is None:
        return
    if holiday_blocks_driver(driver.id, holiday):
        raise ValidationError(
            f"Cannot schedule a shift on {holiday.name}: driver is not allowed to work this holiday"
        )
