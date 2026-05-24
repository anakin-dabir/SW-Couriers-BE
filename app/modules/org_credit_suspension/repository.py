"""Repositories for OrgCreditConfig and OrgSuspensionConfig."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import NotFoundError
from app.common.repository import BaseRepository
from app.modules.org_credit_suspension.models import OrgCreditConfig, OrgSuspensionConfig


class OrgCreditConfigRepository(BaseRepository):
    """Repository for OrgCreditConfig (one-to-one with Organization)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditConfig)

    async def get_by_org(self, organization_id: str) -> OrgCreditConfig | None:
        stmt = select(OrgCreditConfig).where(OrgCreditConfig.organization_id == organization_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_org_or_404(self, organization_id: str) -> OrgCreditConfig:
        config = await self.get_by_org(organization_id)
        if config is None:
            raise NotFoundError(resource="OrgCreditConfig", id=organization_id)
        return config


class OrgSuspensionConfigRepository(BaseRepository):
    """Repository for OrgSuspensionConfig (one-to-one with Organization)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgSuspensionConfig)

    async def get_by_org(self, organization_id: str) -> OrgSuspensionConfig | None:
        stmt = select(OrgSuspensionConfig).where(OrgSuspensionConfig.organization_id == organization_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_org_or_404(self, organization_id: str) -> OrgSuspensionConfig:
        config = await self.get_by_org(organization_id)
        if config is None:
            raise NotFoundError(resource="OrgSuspensionConfig", id=organization_id)
        return config
