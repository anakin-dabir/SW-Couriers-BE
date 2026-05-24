"""Dashboard v1 routes."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from math import ceil
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.deps import Allowed, AuthUser
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.response import ok
from app.common.schemas import SuccessResponse
from app.modules.dashboard.access import resolve_dashboard_organization_id
from app.modules.dashboard.financial_service import DashboardFinancialService
from app.modules.dashboard.highlighted_issues_service import HighlightedIssuesService
from app.modules.dashboard.operations_service import OperationsDashboardService
from app.modules.dashboard.v1.docs import HIGHLIGHTED_ISSUES, OPERATIONS_DASHBOARD_KPIS, TODAYS_FINANCIALS
from app.modules.dashboard.v1.schemas import (
    DashboardCountKpi,
    DeliveredTodayKpi,
    HighlightedIssueItem,
    HighlightedIssuesListResponse,
    OperationsDashboardKpisResponse,
    RevenueTrendDay,
    TodaysFinancialsResponse,
)

router = APIRouter()

DashboardReadDep = Annotated[
    AuthUser,
    Allowed(
        UserRole.SUPER_ADMIN,
        UserRole.ADMIN,
        resource=Resource.DASHBOARD,
        level=PermissionLevel.READ,
    ),
]
OperationsDashboardServiceDep = Annotated[OperationsDashboardService, Depends(OperationsDashboardService.dep)]
DashboardFinancialServiceDep = Annotated[DashboardFinancialService, Depends(DashboardFinancialService.dep)]
HighlightedIssuesServiceDep = Annotated[HighlightedIssuesService, Depends(HighlightedIssuesService.dep)]


def _count_kpi(kpi) -> DashboardCountKpi:
    return DashboardCountKpi(
        current=kpi.current,
        previous=kpi.previous,
        change_abs=kpi.change_abs,
        change_pct=kpi.change_pct,
        comparison_label=kpi.comparison_label,
    )


@router.get(
    "/operations-kpis",
    response_model=SuccessResponse[OperationsDashboardKpisResponse],
    **OPERATIONS_DASHBOARD_KPIS,  # type: ignore[arg-type]
)
async def get_operations_dashboard_kpis(
    user: DashboardReadDep,
    service: OperationsDashboardServiceDep,
    organization_id: str | None = Query(
        default=None,
        description="Scope metrics to one organisation; omit for global admin totals",
    ),
    as_of_date: date | None = Query(
        default=None,
        description="Reference calendar day (UTC) for KPI windows; defaults to today",
    ),
) -> dict:
    org_id = resolve_dashboard_organization_id(user, organization_id)
    result = await service.get_operations_kpis(organization_id=org_id, as_of_date=as_of_date)
    delivered = result.delivered_today
    return ok(
        data=OperationsDashboardKpisResponse(
            as_of_date=result.as_of_date,
            organization_id=result.organization_id,
            next_7_day_stops=_count_kpi(result.next_7_day_stops),
            delivered_today=DeliveredTodayKpi(
                current=delivered.current,
                previous=delivered.previous,
                change_abs=delivered.change_abs,
                change_pct=delivered.change_pct,
                success_rate_pct=delivered.success_rate_pct,
                previous_success_rate_pct=delivered.previous_success_rate_pct,
                comparison_label=delivered.comparison_label,
            ),
            today_orders=_count_kpi(result.today_orders),
            pending_orders=_count_kpi(result.pending_orders),
            active_drivers=_count_kpi(result.active_drivers),
        )
    )


@router.get(
    "/todays-financials",
    response_model=SuccessResponse[TodaysFinancialsResponse],
    **TODAYS_FINANCIALS,  # type: ignore[arg-type]
)
async def get_todays_financials(
    user: DashboardReadDep,
    service: DashboardFinancialServiceDep,
    organization_id: str | None = Query(default=None),
    as_of_date: date | None = Query(default=None),
) -> dict:
    org_id = resolve_dashboard_organization_id(user, organization_id)
    result = await service.get_todays_financials(organization_id=org_id, as_of_date=as_of_date)
    return ok(
        data=TodaysFinancialsResponse(
            as_of_date=result.as_of_date,
            organization_id=result.organization_id,
            revenue_today=str(result.revenue_today),
            unpaid_invoices_count=result.unpaid_invoices_count,
            overdue_invoices_count=result.overdue_invoices_count,
            revenue_trend=[
                RevenueTrendDay(date=day.date, weekday=day.weekday, revenue=str(day.revenue))
                for day in result.revenue_trend
            ],
        )
    )


@router.get(
    "/highlighted-issues",
    response_model=SuccessResponse[HighlightedIssuesListResponse],
    **HIGHLIGHTED_ISSUES,  # type: ignore[arg-type]
)
async def list_highlighted_issues(
    user: DashboardReadDep,
    service: HighlightedIssuesServiceDep,
    organization_id: str | None = Query(default=None),
    search: str | None = Query(default=None, description="Tracking id, client name, order ref, or org name"),
    status: list[str] | None = Query(default=None, description="Filter by delivery stop status (repeatable)"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    as_of_date: date | None = Query(default=None),
) -> dict:
    org_id = resolve_dashboard_organization_id(user, organization_id)
    rows, total = await service.list_highlighted_issues(
        org_id,
        search=search,
        status=status,
        page=page,
        size=size,
        as_of_date=as_of_date,
    )
    pages = ceil(total / size) if total else 0
    return ok(
        data=HighlightedIssuesListResponse(
            items=[HighlightedIssueItem.model_validate(asdict(row)) for row in rows],
            total=total,
            page=page,
            size=size,
            pages=pages,
        )
    )
