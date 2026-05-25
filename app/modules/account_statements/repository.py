"""Account statement repositories."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.repository import BaseRepository
from app.modules.account_statements.enums import (
    StatementDeliveryStatus,
    StatementPdfStatus,
    StatementScheduleStatus,
)
from app.modules.account_statements.models import (
    AccountStatement,
    AccountStatementDeliveryEvent,
    AccountStatementSchedule,
)


class AccountStatementRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AccountStatement)

    async def list_for_org(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        period_start_from: date | None = None,
        period_start_to: date | None = None,
        generated_from: datetime | None = None,
        generated_to: datetime | None = None,
        include_deleted: bool = False,
    ) -> tuple[list[AccountStatement], int]:
        stmt = select(AccountStatement)
        count_stmt = select(func.count()).select_from(AccountStatement)
        stmt = self._apply_where(stmt, organization_id=organization_id)
        count_stmt = self._apply_where(count_stmt, organization_id=organization_id)
        if not include_deleted:
            stmt = stmt.where(AccountStatement.deleted_at.is_(None))
            count_stmt = count_stmt.where(AccountStatement.deleted_at.is_(None))
        if search:
            term = f"%{search.strip()}%"
            stmt = stmt.where(AccountStatement.statement_number.ilike(term))
            count_stmt = count_stmt.where(AccountStatement.statement_number.ilike(term))
        if period_start_from is not None:
            stmt = stmt.where(AccountStatement.period_start >= period_start_from)
            count_stmt = count_stmt.where(AccountStatement.period_start >= period_start_from)
        if period_start_to is not None:
            stmt = stmt.where(AccountStatement.period_start <= period_start_to)
            count_stmt = count_stmt.where(AccountStatement.period_start <= period_start_to)
        if generated_from is not None:
            stmt = stmt.where(AccountStatement.created_at >= generated_from)
            count_stmt = count_stmt.where(AccountStatement.created_at >= generated_from)
        if generated_to is not None:
            stmt = stmt.where(AccountStatement.created_at <= generated_to)
            count_stmt = count_stmt.where(AccountStatement.created_at <= generated_to)

        total = int((await self.session.execute(count_stmt)).scalar_one())

        stmt = stmt.order_by(AccountStatement.created_at.desc()).offset((page - 1) * size).limit(size)
        items = list((await self.session.execute(stmt)).scalars().all())
        return items, total

    async def get_active_by_signature(
        self,
        organization_id: str,
        content_signature: str,
    ) -> AccountStatement | None:
        stmt = select(AccountStatement)
        stmt = self._apply_where(stmt, organization_id=organization_id, content_signature=content_signature)
        stmt = stmt.where(
            AccountStatement.deleted_at.is_(None),
            AccountStatement.pdf_status.in_(
                [StatementPdfStatus.PENDING.value, StatementPdfStatus.GENERATING.value, StatementPdfStatus.READY.value]
            ),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def has_successful_delivery(self, statement_id: str) -> bool:
        stmt = select(func.count()).select_from(AccountStatementDeliveryEvent).where(
            AccountStatementDeliveryEvent.statement_id == statement_id,
            AccountStatementDeliveryEvent.status == StatementDeliveryStatus.SENT.value,
        )
        return int((await self.session.execute(stmt)).scalar_one()) > 0


class AccountStatementScheduleRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AccountStatementSchedule)

    async def list_for_org(self, organization_id: str) -> list[AccountStatementSchedule]:
        stmt = select(AccountStatementSchedule)
        stmt = self._apply_where(stmt, organization_id=organization_id)
        stmt = stmt.order_by(AccountStatementSchedule.created_at.desc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_due(self, *, before: datetime, limit: int = 50) -> list[AccountStatementSchedule]:
        stmt = (
            select(AccountStatementSchedule)
            .where(
                AccountStatementSchedule.status == StatementScheduleStatus.ACTIVE.value,
                AccountStatementSchedule.next_run_at.isnot(None),
                AccountStatementSchedule.next_run_at <= before,
            )
            .order_by(AccountStatementSchedule.next_run_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list((await self.session.execute(stmt)).scalars().all())


class AccountStatementDeliveryRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AccountStatementDeliveryEvent)
