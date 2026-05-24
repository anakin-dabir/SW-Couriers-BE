from __future__ import annotations

from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.repository import BaseRepository
from app.modules.pickup_addresses.models import PickupAddress


class PickupAddressRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PickupAddress)

    async def list_for_scope(
        self,
        *,
        organization_id: str | None,
        user_id: str | None,
    ) -> list[PickupAddress]:
        stmt: Select = select(PickupAddress).order_by(PickupAddress.created_at.asc())
        if organization_id:
            stmt = stmt.where(PickupAddress.organization_id == organization_id)
        elif user_id:
            stmt = stmt.where(PickupAddress.user_id == user_id)
        else:
            return []
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_scope_or_none(
        self,
        address_id: str,
        *,
        organization_id: str | None,
        user_id: str | None,
    ) -> PickupAddress | None:
        stmt = select(PickupAddress).where(PickupAddress.id == address_id)
        if organization_id:
            stmt = stmt.where(PickupAddress.organization_id == organization_id)
        elif user_id:
            stmt = stmt.where(PickupAddress.user_id == user_id)
        else:
            return None
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_for_scope(self, scope: dict[str, Any]) -> int:
        return await self.count(**scope)

    async def clear_default_for_scope(self, *, organization_id: str | None, user_id: str | None) -> None:
        from sqlalchemy import update

        if organization_id:
            await self.session.execute(update(PickupAddress).where(PickupAddress.organization_id == organization_id).values(is_default=False))
        elif user_id:
            await self.session.execute(update(PickupAddress).where(PickupAddress.user_id == user_id).values(is_default=False))
