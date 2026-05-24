"""Repository for service tier configuration."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.repository import BaseRepository
from app.modules.service_tiers.constants import SUPERFAST_AVAILABLE_FOR, SUPERFAST_TIER_NAME
from app.modules.service_tiers.enums import ServiceTierScopeType, ServiceTierStatus
from app.modules.service_tiers.models import ServiceTier


def _escape_ilike_pattern(term: str) -> str:
    """Escape ``%``, ``_``, and ``\\`` for use in ILIKE with PostgreSQL escape ``\\\\``."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ServiceTierRepository(BaseRepository):
    """Data access for ServiceTier records."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ServiceTier)

    async def find_global_superfast(self) -> ServiceTier | None:
        return await self.find_global_by_name_audience(
            tier_name=SUPERFAST_TIER_NAME,
            available_for=SUPERFAST_AVAILABLE_FOR,
        )

    async def find_global_by_name_audience(self, *, tier_name: str, available_for: str) -> ServiceTier | None:
        stmt = select(ServiceTier).where(
            ServiceTier.scope_type == ServiceTierScopeType.GLOBAL.value,
            ServiceTier.scope_org_id.is_(None),
            ServiceTier.tier_name == tier_name,
            ServiceTier.available_for == available_for,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_org_by_name_audience(
        self, *, organization_id: str, tier_name: str, available_for: str
    ) -> ServiceTier | None:
        stmt = select(ServiceTier).where(
            ServiceTier.scope_type == ServiceTierScopeType.ORG.value,
            ServiceTier.scope_org_id == organization_id,
            ServiceTier.tier_name == tier_name,
            ServiceTier.available_for == available_for,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active_global(self) -> list[ServiceTier]:
        stmt = (
            select(ServiceTier)
            .where(
                ServiceTier.scope_type == ServiceTierScopeType.GLOBAL.value,
                ServiceTier.scope_org_id.is_(None),
                ServiceTier.status == ServiceTierStatus.ACTIVE,
            )
            .order_by(ServiceTier.tier_name)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_active_for_org(self, organization_id: str) -> list[ServiceTier]:
        stmt = (
            select(ServiceTier)
            .where(
                ServiceTier.scope_type == ServiceTierScopeType.ORG.value,
                ServiceTier.scope_org_id == organization_id,
                ServiceTier.status == ServiceTierStatus.ACTIVE,
            )
            .order_by(ServiceTier.tier_name)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_filters(
        self,
        *,
        scope_type: str | None = None,
        scope_org_id: str | None = None,
        available_for: list[str] | None = None,
        status: list[str] | None = None,
        search: str | None = None,
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        min_days: int | None = None,
        max_days: int | None = None,
    ) -> list[ServiceTier]:
        """Return all tiers matching the given filters, ordered by tier_name."""
        conditions = []

        if scope_type is not None:
            conditions.append(ServiceTier.scope_type == scope_type)
        if scope_org_id is not None:
            conditions.append(ServiceTier.scope_org_id == scope_org_id)
        if available_for:
            conditions.append(ServiceTier.available_for.in_(available_for))
        if status:
            conditions.append(ServiceTier.status.in_(status))
        if search:
            escaped = _escape_ilike_pattern(search)
            conditions.append(ServiceTier.tier_name.ilike(f"%{escaped}%", escape="\\"))
        if min_price is not None:
            conditions.append(ServiceTier.price_per_package >= min_price)
        if max_price is not None:
            conditions.append(ServiceTier.price_per_package <= max_price)
        if min_days is not None:
            conditions.append(ServiceTier.duration_days >= min_days)
        if max_days is not None:
            conditions.append(ServiceTier.duration_days <= max_days)

        stmt = select(ServiceTier)
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.order_by(ServiceTier.tier_name)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def exists_global_name_audience(self, *, tier_name: str, available_for: str, exclude_id: str | None = None) -> bool:
        stmt = select(func.count()).select_from(ServiceTier).where(
            ServiceTier.scope_type == ServiceTierScopeType.GLOBAL.value,
            ServiceTier.scope_org_id.is_(None),
            ServiceTier.tier_name == tier_name,
            ServiceTier.available_for == available_for,
        )
        if exclude_id is not None:
            stmt = stmt.where(ServiceTier.id != exclude_id)
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0) > 0

    async def exists_org_name_audience(
        self, *, organization_id: str, tier_name: str, available_for: str, exclude_id: str | None = None
    ) -> bool:
        stmt = select(func.count()).select_from(ServiceTier).where(
            ServiceTier.scope_type == ServiceTierScopeType.ORG.value,
            ServiceTier.scope_org_id == organization_id,
            ServiceTier.tier_name == tier_name,
            ServiceTier.available_for == available_for,
        )
        if exclude_id is not None:
            stmt = stmt.where(ServiceTier.id != exclude_id)
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0) > 0
