"""Repositories for holidays and allowed drivers."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.repository import BaseRepository
from app.modules.holidays.models import Holiday, HolidayAllowedDriver


class HolidayRepository(BaseRepository):
    """Data access for Holiday records."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Holiday)

    async def list_year_counts(self) -> list[tuple[int, int]]:
        """Return configured years and their holiday totals."""
        stmt = (
            select(Holiday.year, func.count(Holiday.id))
            .group_by(Holiday.year)
            .order_by(Holiday.year.desc())
        )
        result = await self.session.execute(stmt)
        return [(int(year), int(total)) for year, total in result.all()]


class HolidayAllowedDriverRepository(BaseRepository):
    """Data access for drivers explicitly allowed to work on a holiday."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, HolidayAllowedDriver)
