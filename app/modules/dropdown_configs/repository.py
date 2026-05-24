from __future__ import annotations

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.repository import BaseRepository
from app.modules.dropdown_configs.enums import DropdownConfigKey
from app.modules.dropdown_configs.models import DropdownValue


class DropdownValueRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DropdownValue)

    async def count_for_key(self, dropdown_key: DropdownConfigKey) -> int:
        stmt = select(func.count()).select_from(DropdownValue).where(DropdownValue.dropdown_key == dropdown_key)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def list_for_key(self, dropdown_key: DropdownConfigKey) -> list[DropdownValue]:
        stmt: Select = (
            select(DropdownValue)
            .where(DropdownValue.dropdown_key == dropdown_key)
            .order_by(DropdownValue.code.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self) -> list[DropdownValue]:
        stmt: Select = select(DropdownValue).order_by(
            DropdownValue.dropdown_key.asc(),
            DropdownValue.code.asc(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete_all_for_key(self, dropdown_key: DropdownConfigKey) -> None:
        stmt = delete(DropdownValue).where(DropdownValue.dropdown_key == dropdown_key)
        await self.session.execute(stmt)
        await self.session.flush()
