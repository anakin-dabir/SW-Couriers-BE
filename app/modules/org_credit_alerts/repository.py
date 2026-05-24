from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.repository import BaseRepository
from app.modules.org_credit_alerts.enums import (
    CreditAlertStatus,
    CreditAlertType,
)
from app.modules.org_credit_alerts.models import GlobalCreditAlertThreshold, OrgCreditAlert, OrgCreditAlertConfig

_ACTIVE_STATUSES = (CreditAlertStatus.ACTIVE, CreditAlertStatus.SNOOZED)
_HISTORY_STATUSES = (
    CreditAlertStatus.ACKNOWLEDGED,
    CreditAlertStatus.AUTO_ACKNOWLEDGED,
    CreditAlertStatus.RESOLVED,
)


class GlobalCreditAlertThresholdRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, GlobalCreditAlertThreshold)

    async def list_all(self) -> list[GlobalCreditAlertThreshold]:
        result = await self.session.execute(select(GlobalCreditAlertThreshold))
        return list(result.scalars().all())

    async def get_by_type(self, alert_type: CreditAlertType) -> GlobalCreditAlertThreshold | None:
        return await self.find_one(alert_type=alert_type)


class OrgCreditAlertConfigRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditAlertConfig)

    async def list_for_org(self, organization_id: str) -> list[OrgCreditAlertConfig]:
        stmt = select(OrgCreditAlertConfig).where(OrgCreditAlertConfig.organization_id == organization_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_org_and_type(
        self,
        organization_id: str,
        alert_type: CreditAlertType,
    ) -> OrgCreditAlertConfig | None:
        return await self.find_one(organization_id=organization_id, alert_type=alert_type)


class OrgCreditAlertRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditAlert)

    async def get_with_user(self, alert_id: str) -> OrgCreditAlert | None:
        stmt = (
            select(OrgCreditAlert)
            .where(OrgCreditAlert.id == alert_id)
            .options(selectinload(OrgCreditAlert.acknowledged_by))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self, organization_id: str, *, limit: int | None = None) -> list[OrgCreditAlert]:
        stmt = (
            select(OrgCreditAlert)
            .where(
                OrgCreditAlert.organization_id == organization_id,
                OrgCreditAlert.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(desc(OrgCreditAlert.triggered_at))
            .options(selectinload(OrgCreditAlert.acknowledged_by))
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_active(self, organization_id: str) -> int:
        stmt = select(func.count()).select_from(OrgCreditAlert).where(
            OrgCreditAlert.organization_id == organization_id,
            OrgCreditAlert.status.in_(_ACTIVE_STATUSES),
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def count_unacknowledged(self, organization_id: str) -> int:
        stmt = select(func.count()).select_from(OrgCreditAlert).where(
            OrgCreditAlert.organization_id == organization_id,
            OrgCreditAlert.status == CreditAlertStatus.ACTIVE,
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def last_triggered_at(self, organization_id: str) -> datetime | None:
        stmt = select(func.max(OrgCreditAlert.triggered_at)).where(
            OrgCreditAlert.organization_id == organization_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_history(
        self,
        organization_id: str,
        *,
        page: int,
        size: int,
        statuses: list[CreditAlertStatus] | None = None,
        alert_types: list[CreditAlertType] | None = None,
    ) -> tuple[list[OrgCreditAlert], int]:
        base_filters = [
            OrgCreditAlert.organization_id == organization_id,
            OrgCreditAlert.status.in_(statuses or _HISTORY_STATUSES),
        ]
        if alert_types:
            base_filters.append(OrgCreditAlert.alert_type.in_(alert_types))

        stmt = (
            select(OrgCreditAlert)
            .where(and_(*base_filters))
            .order_by(desc(OrgCreditAlert.triggered_at))
            .offset((page - 1) * size)
            .limit(size)
            .options(selectinload(OrgCreditAlert.acknowledged_by))
        )
        count_stmt = select(func.count()).select_from(OrgCreditAlert).where(and_(*base_filters))

        total = (await self.session.execute(count_stmt)).scalar_one()
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), int(total)

    async def find_open_for_type(
        self,
        organization_id: str,
        alert_type: CreditAlertType,
    ) -> OrgCreditAlert | None:
        stmt = (
            select(OrgCreditAlert)
            .where(
                OrgCreditAlert.organization_id == organization_id,
                OrgCreditAlert.alert_type == alert_type,
                OrgCreditAlert.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(desc(OrgCreditAlert.triggered_at))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_last_for_type(
        self,
        organization_id: str,
        alert_type: CreditAlertType,
    ) -> OrgCreditAlert | None:
        stmt = (
            select(OrgCreditAlert)
            .where(
                OrgCreditAlert.organization_id == organization_id,
                OrgCreditAlert.alert_type == alert_type,
            )
            .order_by(desc(OrgCreditAlert.triggered_at))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_snoozed_expired(self, now: datetime | None = None) -> list[OrgCreditAlert]:
        now_ts = now or datetime.now(UTC)
        stmt = select(OrgCreditAlert).where(
            OrgCreditAlert.status == CreditAlertStatus.SNOOZED,
            OrgCreditAlert.snoozed_until.is_not(None),
            OrgCreditAlert.snoozed_until <= now_ts,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
