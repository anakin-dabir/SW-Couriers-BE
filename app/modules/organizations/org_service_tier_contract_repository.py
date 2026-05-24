"""Data access for org service tier contract lines."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.repository import BaseRepository
from app.modules.organizations.models import OrgServiceTierContractLine


class OrgServiceTierContractRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgServiceTierContractLine)

    async def delete_for_organization(self, organization_id: str) -> None:
        await self.session.execute(delete(OrgServiceTierContractLine).where(OrgServiceTierContractLine.organization_id == organization_id))

    async def list_for_organization(self, organization_id: str) -> list[OrgServiceTierContractLine]:
        stmt = (
            select(OrgServiceTierContractLine)
            .where(OrgServiceTierContractLine.organization_id == organization_id)
            .order_by(OrgServiceTierContractLine.sort_order, OrgServiceTierContractLine.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
