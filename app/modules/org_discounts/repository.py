"""Repository for OrgDiscountConfig (per-org, per-service-tier, per-discount-type rows)."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import NotFoundError
from app.common.repository import BaseRepository
from app.modules.org_discounts.models import OrgDiscountConfig


class OrgDiscountConfigRepository(BaseRepository):
    """Repository for OrgDiscountConfig.

    One row per (organization_id, service_tier_id, discount_type).
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgDiscountConfig)

    async def list_by_org(self, organization_id: str) -> list[OrgDiscountConfig]:
        """Return all discount config rows for an org, ordered by tier then type."""
        stmt = (
            select(OrgDiscountConfig)
            .where(OrgDiscountConfig.organization_id == organization_id)
            .order_by(OrgDiscountConfig.service_tier_id, OrgDiscountConfig.discount_type)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_org_tier_type(
        self,
        organization_id: str,
        service_tier_id: str,
        discount_type: str,
    ) -> OrgDiscountConfig | None:
        """Return the row for a specific (org, tier, type) combination, or None."""
        stmt = select(OrgDiscountConfig).where(
            OrgDiscountConfig.organization_id == organization_id,
            OrgDiscountConfig.service_tier_id == service_tier_id,
            OrgDiscountConfig.discount_type == discount_type,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_all_by_org(self, organization_id: str) -> None:
        """Hard-delete all discount config rows for an org."""
        stmt = delete(OrgDiscountConfig).where(OrgDiscountConfig.organization_id == organization_id)
        await self.session.execute(stmt)
