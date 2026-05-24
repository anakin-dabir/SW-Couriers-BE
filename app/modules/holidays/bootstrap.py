"""Startup bootstrap helpers for holiday defaults."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.holidays.defaults import build_universal_uk_holidays
from app.modules.holidays.repository import HolidayRepository


async def seed_universal_uk_holidays_for_current_year(session: AsyncSession) -> int:
    """Seed UK holiday defaults for the current year when that year is empty."""
    current_year = date.today().year
    repo = HolidayRepository(session)
    existing_count = await repo.count(year=current_year)
    if existing_count > 0:
        return 0

    for item in build_universal_uk_holidays(current_year):
        await repo.create(item)
    return 8
