"""Today's financials dashboard service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.schemas import quantize_currency
from app.common.service import BaseService
from app.modules.dashboard.financial_repository import DashboardFinancialRepository
from app.modules.dashboard.types import RevenueTrendDayResult, TodaysFinancialsResult
from app.modules.dashboard.validation import resolve_as_of_date, validate_as_of_date


class DashboardFinancialService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._repo = DashboardFinancialRepository(session)

    async def get_todays_financials(
        self,
        *,
        organization_id: str | None,
        as_of_date: date | None = None,
    ) -> TodaysFinancialsResult:
        today = validate_as_of_date(resolve_as_of_date(as_of_date))
        trend_start = today - timedelta(days=6)
        by_day = await self._repo.payments_by_day(
            organization_id,
            start=trend_start,
            end=today,
        )
        trend: list[RevenueTrendDayResult] = []
        for offset in range(7):
            day = trend_start + timedelta(days=offset)
            trend.append(
                RevenueTrendDayResult(
                    date=day,
                    weekday=day.strftime("%A"),
                    revenue=quantize_currency(by_day.get(day, Decimal("0"))),
                )
            )
        revenue_today = await self._repo.payments_total(organization_id, start=today, end=today)
        unpaid, overdue = await self._repo.invoice_collection_counts(organization_id, as_of=today)
        return TodaysFinancialsResult(
            as_of_date=today,
            organization_id=organization_id,
            revenue_today=quantize_currency(revenue_today),
            unpaid_invoices_count=unpaid,
            overdue_invoices_count=overdue,
            revenue_trend=tuple(trend),
        )
