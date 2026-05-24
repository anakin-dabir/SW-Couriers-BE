"""Org billing overview — single API for dashboard KPIs and charts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.service import BaseService
from app.modules.billing.metrics import pct_change, resolve_billing_overview_window
from app.modules.billing.overview_repository import BillingOverviewRepository


class BillingOverviewService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._repo = BillingOverviewRepository(session)

    async def get_overview(
        self,
        *,
        organization_id: str,
        period: str | None = None,
        chart_year: int | None = None,
        today: date | None = None,
    ) -> dict:
        window, meta = resolve_billing_overview_window(period=period, today=today)
        year = chart_year or (today or date.today()).year

        cur_billed = await self._repo.total_billed(
            organization_id=organization_id,
            start=window.current_from,
            end=window.current_to,
        )
        prev_billed = await self._repo.total_billed(
            organization_id=organization_id,
            start=window.previous_from,
            end=window.previous_to,
        )
        cur_payments = await self._repo.payments_received(
            organization_id=organization_id,
            start=window.current_from,
            end=window.current_to,
        )
        prev_payments = await self._repo.payments_received(
            organization_id=organization_id,
            start=window.previous_from,
            end=window.previous_to,
        )
        outstanding, overdue = await self._repo.outstanding_and_overdue_as_of(
            organization_id=organization_id,
            as_of=window.current_to,
        )
        prev_outstanding, prev_overdue = await self._repo.outstanding_and_overdue_as_of(
            organization_id=organization_id,
            as_of=window.previous_to,
        )
        cur_cn = await self._repo.credit_notes_issued_count(
            organization_id=organization_id,
            start=window.current_from,
            end=window.current_to,
        )
        prev_cn = await self._repo.credit_notes_issued_count(
            organization_id=organization_id,
            start=window.previous_from,
            end=window.previous_to,
        )
        cur_refunds = await self._repo.refunds_issued(
            organization_id=organization_id,
            start=window.current_from,
            end=window.current_to,
        )
        prev_refunds = await self._repo.refunds_issued(
            organization_id=organization_id,
            start=window.previous_from,
            end=window.previous_to,
        )

        def kpi(value: Decimal | int, previous: Decimal | int) -> dict:
            cur_d = Decimal(str(value))
            prev_d = Decimal(str(previous))
            change = pct_change(cur_d, prev_d)
            if isinstance(value, Decimal):
                value_str = str(cur_d.quantize(Decimal("0.01")))
            else:
                value_str = str(int(value))
            return {
                "value": value_str,
                "change_pct": str(change) if change is not None else None,
                "comparison_label": meta.comparison_label,
            }

        buckets = await self._repo.payment_method_buckets(
            organization_id=organization_id,
            start=window.current_from,
            end=window.current_to,
        )
        total_pay = sum(buckets.values()) or Decimal("0")
        payment_methods = []
        for key in ("CARD", "BANK_TRANSFER", "CASH"):
            amt = buckets.get(key, Decimal("0"))
            pct = (amt / total_pay * Decimal("100")).quantize(Decimal("0.01")) if total_pay > 0 else Decimal("0")
            payment_methods.append({"method": key, "amount": str(amt), "percent": str(pct)})

        return {
            "meta": {
                "period_start": meta.period_start.isoformat(),
                "period_end": meta.period_end.isoformat(),
                "prior_period_start": meta.prior_period_start.isoformat(),
                "prior_period_end": meta.prior_period_end.isoformat(),
                "timezone": meta.timezone,
                "definitions_version": meta.definitions_version,
                "chart_year": year,
            },
            "kpis": {
                "total_billed": kpi(cur_billed.amount, prev_billed.amount),
                "payments_received": kpi(cur_payments.amount, prev_payments.amount),
                "outstanding_balance": kpi(outstanding, prev_outstanding),
                "overdue_amount": kpi(overdue, prev_overdue),
                "credit_notes_issued": kpi(cur_cn, prev_cn),
                "refunds_issued": kpi(cur_refunds.amount, prev_refunds.amount),
            },
            "charts": {
                "revenue_trend": await self._repo.revenue_by_month(organization_id=organization_id, year=year),
                "payment_method_usage": payment_methods,
                "invoice_status": await self._repo.invoice_status_breakdown(organization_id=organization_id, year=year),
                "billing_activity": await self._repo.billing_activity_by_month(organization_id=organization_id, year=year),
            },
        }
