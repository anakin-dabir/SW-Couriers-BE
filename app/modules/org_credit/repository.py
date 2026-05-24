from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.common.repository import BaseRepository
from app.modules.org_credit.enums import OrgCreditInvestigationStatus, OrgCreditLedgerMovementType
from app.modules.org_credit.models import (
    OrgCreditAccount,
    OrgCreditInternalScoreHistory,
    OrgCreditInvestigation,
    OrgCreditLedgerEntry,
    OrgCreditReport,
    OrgCreditStatusHistory,
)
from app.modules.user.models import User


class OrgCreditReportRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditReport)

    async def get_by_org_id(self, organization_id: str) -> OrgCreditReport | None:
        stmt = select(OrgCreditReport).where(OrgCreditReport.organization_id == organization_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_and_org(self, report_id: str, organization_id: str) -> OrgCreditReport | None:
        stmt = select(OrgCreditReport).where(
            OrgCreditReport.id == report_id,
            OrgCreditReport.organization_id == organization_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def upsert_for_org(self, organization_id: str, data: dict[str, Any]) -> OrgCreditReport:
        existing = await self.get_by_org_id(organization_id)
        if existing:
            for key, value in data.items():
                setattr(existing, key, value)
            await self.session.flush()
            await self.session.refresh(existing)
            return existing
        data["organization_id"] = organization_id
        return await self.create(data)


class OrgCreditAccountRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditAccount)

    async def get_by_org_id(self, organization_id: str) -> OrgCreditAccount | None:
        stmt = (
            select(OrgCreditAccount)
            .where(OrgCreditAccount.organization_id == organization_id)
            .options(joinedload(OrgCreditAccount.action_by_user))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_org_id_for_update(self, organization_id: str) -> OrgCreditAccount | None:
        stmt = (
            select(OrgCreditAccount)
            .where(OrgCreditAccount.organization_id == organization_id)
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_org_ids_with_due_pending_settings(self, today: date) -> list[str]:
        stmt = select(OrgCreditAccount.organization_id).where(
            or_(
                (
                    OrgCreditAccount.pending_credit_limit_effective_from.isnot(None)
                    & (OrgCreditAccount.pending_credit_limit_effective_from <= today)
                ),
                (
                    OrgCreditAccount.pending_payment_terms_effective_from.isnot(None)
                    & (OrgCreditAccount.pending_payment_terms_effective_from <= today)
                ),
            ),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class OrgCreditLedgerRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditLedgerEntry)

    async def get_by_org_and_idempotency_key(
        self,
        organization_id: str,
        idempotency_key: str,
    ) -> OrgCreditLedgerEntry | None:
        stmt = select(OrgCreditLedgerEntry).where(
            OrgCreditLedgerEntry.organization_id == organization_id,
            OrgCreditLedgerEntry.idempotency_key == idempotency_key,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_org(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
        movement_type: OrgCreditLedgerMovementType | None = None,
    ) -> tuple[list[OrgCreditLedgerEntry], int]:
        stmt = select(OrgCreditLedgerEntry).where(OrgCreditLedgerEntry.organization_id == organization_id)
        count_stmt = select(func.count()).select_from(OrgCreditLedgerEntry).where(
            OrgCreditLedgerEntry.organization_id == organization_id,
        )
        if movement_type is not None:
            stmt = stmt.where(OrgCreditLedgerEntry.movement_type == movement_type)
            count_stmt = count_stmt.where(OrgCreditLedgerEntry.movement_type == movement_type)

        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        stmt = stmt.order_by(OrgCreditLedgerEntry.created_at.desc())
        offset = (page - 1) * size
        stmt = stmt.offset(offset).limit(size)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def list_activity_with_actor(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[Row], int]:
        base_filter = [
            OrgCreditLedgerEntry.organization_id == organization_id,
        ]
        count_stmt = (
            select(func.count())
            .select_from(OrgCreditLedgerEntry)
            .where(*base_filter)
        )
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(
                OrgCreditLedgerEntry,
                User.first_name.label("actor_first_name"),
                User.last_name.label("actor_last_name"),
                User.email.label("actor_email"),
            )
            .outerjoin(User, OrgCreditLedgerEntry.actor_user_id == User.id)
            .where(*base_filter)
            .order_by(OrgCreditLedgerEntry.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        rows = (await self.session.execute(stmt)).all()
        return list(rows), total

    async def list_utilisation_snapshots(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
        created_at_min: datetime | None = None,
        created_at_max: datetime | None = None,
    ) -> tuple[list[OrgCreditLedgerEntry], int]:
        base_filter = [
            OrgCreditLedgerEntry.organization_id == organization_id,
        ]
        if created_at_min is not None:
            base_filter.append(OrgCreditLedgerEntry.created_at >= created_at_min)
        if created_at_max is not None:
            base_filter.append(OrgCreditLedgerEntry.created_at <= created_at_max)
        count_stmt = (
            select(func.count())
            .select_from(OrgCreditLedgerEntry)
            .where(*base_filter)
        )
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(OrgCreditLedgerEntry)
            .where(*base_filter)
            .order_by(OrgCreditLedgerEntry.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def list_in_range_asc(
        self,
        organization_id: str,
        *,
        start: datetime,
        end: datetime,
    ) -> list[OrgCreditLedgerEntry]:
        """All money-movement entries for an org inside the datetime window,
        ordered by ``created_at`` ascending. Used to reconstruct utilisation
        and outstanding trend lines from the ledger's post-state columns.
        """
        stmt = (
            select(OrgCreditLedgerEntry)
            .where(
                OrgCreditLedgerEntry.organization_id == organization_id,
                OrgCreditLedgerEntry.created_at >= start,
                OrgCreditLedgerEntry.created_at <= end,
            )
            .order_by(OrgCreditLedgerEntry.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class OrgCreditStatusHistoryRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditStatusHistory)

    async def latest_for_org(self, organization_id: str) -> OrgCreditStatusHistory | None:
        stmt = (
            select(OrgCreditStatusHistory)
            .where(OrgCreditStatusHistory.organization_id == organization_id)
            .order_by(OrgCreditStatusHistory.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_org(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[OrgCreditStatusHistory], int]:
        base_filter = [OrgCreditStatusHistory.organization_id == organization_id]
        count_stmt = select(func.count()).select_from(OrgCreditStatusHistory).where(*base_filter)
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(OrgCreditStatusHistory)
            .where(*base_filter)
            .options(joinedload(OrgCreditStatusHistory.actor_user))
            .order_by(OrgCreditStatusHistory.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return list(rows), total

    async def list_for_org_with_next_change_at(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[tuple[OrgCreditStatusHistory, datetime | None]], int]:
        base_filter = [OrgCreditStatusHistory.organization_id == organization_id]
        count_stmt = select(func.count()).select_from(OrgCreditStatusHistory).where(*base_filter)
        total = (await self.session.execute(count_stmt)).scalar_one()

        next_change_at = func.lead(OrgCreditStatusHistory.created_at).over(
            partition_by=OrgCreditStatusHistory.organization_id,
            order_by=OrgCreditStatusHistory.created_at.asc(),
        ).label("next_change_at")

        wnd = (
            select(OrgCreditStatusHistory.id.label("hist_id"), next_change_at)
            .where(*base_filter)
            .subquery()
        )

        stmt = (
            select(OrgCreditStatusHistory, wnd.c.next_change_at)
            .join(wnd, OrgCreditStatusHistory.id == wnd.c.hist_id)
            .where(*base_filter)
            .options(joinedload(OrgCreditStatusHistory.actor_user))
            .order_by(OrgCreditStatusHistory.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(r[0], r[1]) for r in rows], total


class OrgCreditInternalScoreHistoryRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditInternalScoreHistory)

    async def latest_for_org(self, organization_id: str) -> OrgCreditInternalScoreHistory | None:
        stmt = (
            select(OrgCreditInternalScoreHistory)
            .where(OrgCreditInternalScoreHistory.organization_id == organization_id)
            .order_by(OrgCreditInternalScoreHistory.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_org_range(
        self,
        organization_id: str,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> list[OrgCreditInternalScoreHistory]:
        stmt = select(OrgCreditInternalScoreHistory).where(
            OrgCreditInternalScoreHistory.organization_id == organization_id,
        )
        if start is not None:
            stmt = stmt.where(func.date(OrgCreditInternalScoreHistory.created_at) >= start)
        if end is not None:
            stmt = stmt.where(func.date(OrgCreditInternalScoreHistory.created_at) <= end)
        stmt = stmt.order_by(OrgCreditInternalScoreHistory.created_at.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class OrgCreditInvestigationRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditInvestigation)

    async def get_active_for_application(
        self,
        organization_id: str,
        application_id: str,
    ) -> OrgCreditInvestigation | None:
        stmt = (
            select(OrgCreditInvestigation)
            .where(
                OrgCreditInvestigation.organization_id == organization_id,
                OrgCreditInvestigation.application_id == application_id,
                OrgCreditInvestigation.status == OrgCreditInvestigationStatus.IN_PROGRESS,
            )
            .order_by(OrgCreditInvestigation.requested_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_for_org(self, organization_id: str) -> OrgCreditInvestigation | None:
        stmt = (
            select(OrgCreditInvestigation)
            .where(
                OrgCreditInvestigation.organization_id == organization_id,
                OrgCreditInvestigation.status == OrgCreditInvestigationStatus.IN_PROGRESS,
            )
            .order_by(OrgCreditInvestigation.requested_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
