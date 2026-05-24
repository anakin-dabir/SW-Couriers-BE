from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.common.repository import BaseRepository
from app.modules.org_credit_settings.constants import GLOBAL_CREDIT_COOLDOWN_ROW_ID
from app.modules.org_credit_settings.enums import ScheduledCreditSettingStatus
from app.modules.org_credit_settings.models import (
    GlobalCreditAccountCooldownPeriod,
    OrgCreditAccountCooldownPeriod,
    OrgCreditCooldownWindow,
    OrgCreditLimitAdjustmentHistory,
    OrgCreditTermsModificationHistory,
)


class GlobalCreditAccountCooldownPeriodRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, GlobalCreditAccountCooldownPeriod)

    async def get_singleton(self) -> GlobalCreditAccountCooldownPeriod | None:
        stmt = select(GlobalCreditAccountCooldownPeriod).where(
            GlobalCreditAccountCooldownPeriod.id == GLOBAL_CREDIT_COOLDOWN_ROW_ID,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_singleton(
        self,
        *,
        months: int | None,
        days: int | None,
        hours: int | None,
    ) -> GlobalCreditAccountCooldownPeriod:
        existing = await self.get_singleton()
        if existing:
            existing.months = months
            existing.days = days
            existing.hours = hours
            await self.session.flush()
            await self.session.refresh(existing)
            return existing
        return await self.create({
            "id": GLOBAL_CREDIT_COOLDOWN_ROW_ID,
            "months": months,
            "days": days,
            "hours": hours,
        })


class OrgCreditAccountCooldownPeriodRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditAccountCooldownPeriod)

    async def get_by_org_id(self, organization_id: str) -> OrgCreditAccountCooldownPeriod | None:
        stmt = select(OrgCreditAccountCooldownPeriod).where(
            OrgCreditAccountCooldownPeriod.organization_id == organization_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_for_org(
        self,
        organization_id: str,
        *,
        months: int,
        days: int,
        hours: int,
    ) -> OrgCreditAccountCooldownPeriod:
        existing = await self.get_by_org_id(organization_id)
        if existing:
            existing.months = months
            existing.days = days
            existing.hours = hours
            await self.session.flush()
            await self.session.refresh(existing)
            return existing
        return await self.create({
            "organization_id": organization_id,
            "months": months,
            "days": days,
            "hours": hours,
        })

    async def delete_for_org(self, organization_id: str) -> None:
        stmt = delete(OrgCreditAccountCooldownPeriod).where(
            OrgCreditAccountCooldownPeriod.organization_id == organization_id,
        )
        await self.session.execute(stmt)
        await self.session.flush()


class OrgCreditCooldownWindowRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditCooldownWindow)

    async def get_by_org_id(self, organization_id: str) -> OrgCreditCooldownWindow | None:
        stmt = select(OrgCreditCooldownWindow).where(
            OrgCreditCooldownWindow.organization_id == organization_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_for_org(
        self,
        organization_id: str,
        *,
        started_at: datetime,
        ends_at: datetime,
        policy_months: int,
        policy_days: int,
        policy_hours: int,
    ) -> OrgCreditCooldownWindow:
        existing = await self.get_by_org_id(organization_id)
        if existing:
            existing.started_at = started_at
            existing.ends_at = ends_at
            existing.policy_months = policy_months
            existing.policy_days = policy_days
            existing.policy_hours = policy_hours
            await self.session.flush()
            await self.session.refresh(existing)
            return existing
        return await self.create({
            "organization_id": organization_id,
            "started_at": started_at,
            "ends_at": ends_at,
            "policy_months": policy_months,
            "policy_days": policy_days,
            "policy_hours": policy_hours,
        })

    async def delete_for_org(self, organization_id: str) -> None:
        stmt = delete(OrgCreditCooldownWindow).where(
            OrgCreditCooldownWindow.organization_id == organization_id,
        )
        await self.session.execute(stmt)
        await self.session.flush()


class OrgCreditTermsModificationHistoryRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditTermsModificationHistory)

    async def list_by_organization_with_actor(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[OrgCreditTermsModificationHistory], int]:
        base_filter = OrgCreditTermsModificationHistory.organization_id == organization_id
        count_stmt = (
            select(func.count())
            .select_from(OrgCreditTermsModificationHistory)
            .where(base_filter)
        )
        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = (
            select(OrgCreditTermsModificationHistory)
            .where(base_filter)
            .options(joinedload(OrgCreditTermsModificationHistory.modified_by_user))
            .order_by(OrgCreditTermsModificationHistory.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        return rows, total

    async def find_scheduled_by_account_and_effective_date(
        self,
        credit_account_id: str,
        effective_date: date,
    ) -> OrgCreditTermsModificationHistory | None:
        stmt = select(OrgCreditTermsModificationHistory).where(
            OrgCreditTermsModificationHistory.credit_account_id == credit_account_id,
            OrgCreditTermsModificationHistory.status == ScheduledCreditSettingStatus.SCHEDULED,
            OrgCreditTermsModificationHistory.effective_date == effective_date,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class OrgCreditLimitAdjustmentHistoryRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditLimitAdjustmentHistory)

    async def list_by_organization_with_actor(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[OrgCreditLimitAdjustmentHistory], int]:
        base_filter = OrgCreditLimitAdjustmentHistory.organization_id == organization_id
        count_stmt = (
            select(func.count())
            .select_from(OrgCreditLimitAdjustmentHistory)
            .where(base_filter)
        )
        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = (
            select(OrgCreditLimitAdjustmentHistory)
            .where(base_filter)
            .options(joinedload(OrgCreditLimitAdjustmentHistory.modified_by_user))
            .order_by(OrgCreditLimitAdjustmentHistory.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        return rows, total

    async def list_applied_in_range(
        self,
        organization_id: str,
        *,
        start: date,
        end: date,
    ) -> list[OrgCreditLimitAdjustmentHistory]:
        stmt = (
            select(OrgCreditLimitAdjustmentHistory)
            .where(
                OrgCreditLimitAdjustmentHistory.organization_id == organization_id,
                OrgCreditLimitAdjustmentHistory.status == ScheduledCreditSettingStatus.APPLIED,
                OrgCreditLimitAdjustmentHistory.effective_date >= start,
                OrgCreditLimitAdjustmentHistory.effective_date <= end,
            )
            .order_by(OrgCreditLimitAdjustmentHistory.effective_date.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_applied(self, organization_id: str) -> OrgCreditLimitAdjustmentHistory | None:
        stmt = (
            select(OrgCreditLimitAdjustmentHistory)
            .where(
                OrgCreditLimitAdjustmentHistory.organization_id == organization_id,
                OrgCreditLimitAdjustmentHistory.status == ScheduledCreditSettingStatus.APPLIED,
            )
            .order_by(
                OrgCreditLimitAdjustmentHistory.effective_date.desc(),
                OrgCreditLimitAdjustmentHistory.created_at.desc(),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_scheduled_by_account_and_effective_date(
        self,
        credit_account_id: str,
        effective_date: date,
    ) -> OrgCreditLimitAdjustmentHistory | None:
        stmt = select(OrgCreditLimitAdjustmentHistory).where(
            OrgCreditLimitAdjustmentHistory.credit_account_id == credit_account_id,
            OrgCreditLimitAdjustmentHistory.status == ScheduledCreditSettingStatus.SCHEDULED,
            OrgCreditLimitAdjustmentHistory.effective_date == effective_date,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
