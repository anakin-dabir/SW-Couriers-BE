"""Org-scoped billing overview routes."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.deps import Allowed, AuthUser
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.response import ok
from app.common.schemas import SuccessResponse
from app.modules.billing.overview_service import BillingOverviewService
from app.modules.billing.v1.overview_docs import BILLING_OVERVIEW_GET
from app.modules.billing.v1.overview_schemas import BillingOverviewPeriodLiteral, BillingOverviewResponse
from app.modules.organizations.access import assert_caller_org_scope

router = APIRouter()

BillingOverviewReadDep = Annotated[
    AuthUser,
    Allowed(
        UserRole.SUPER_ADMIN,
        UserRole.ADMIN,
        resource=Resource.BILLING,
        level=PermissionLevel.READ,
    ),
]
BillingOverviewServiceDep = Annotated[BillingOverviewService, Depends(BillingOverviewService.dep)]


@router.get(
    "/{organization_id}/billing/overview",
    response_model=SuccessResponse[BillingOverviewResponse],
    **BILLING_OVERVIEW_GET,  # type: ignore[arg-type]
)
async def get_billing_overview(
    organization_id: str,
    user: BillingOverviewReadDep,
    service: BillingOverviewServiceDep,
    period: BillingOverviewPeriodLiteral = Query(default="last_30_days"),
    chart_year: int | None = Query(default=None, ge=2000, le=2100),
) -> dict:
    assert_caller_org_scope(user, organization_id)
    raw = await service.get_overview(
        organization_id=organization_id,
        period=period,
        chart_year=chart_year,
        today=date.today(),
    )

    def _serialize_charts(charts: dict) -> dict:
        return {
            "revenue_trend": [
                {
                    "month": m["month"],
                    "revenue": str(m["revenue"]),
                    "refunds": str(m["refunds"]),
                    "net_revenue": str(m["net_revenue"]),
                }
                for m in charts["revenue_trend"]
            ],
            "payment_method_usage": charts["payment_method_usage"],
            "invoice_status": [
                {
                    "status": i["status"],
                    "count": i["count"],
                    "total_value": str(i["total_value"]),
                }
                for i in charts["invoice_status"]
            ],
            "billing_activity": [
                {
                    "month": m["month"],
                    "invoices_amount": str(m["invoices_amount"]),
                    "invoices_count": m["invoices_count"],
                    "payments_amount": str(m["payments_amount"]),
                    "payments_count": m["payments_count"],
                }
                for m in charts["billing_activity"]
            ],
        }

    payload = {
        "meta": raw["meta"],
        "kpis": raw["kpis"],
        "charts": _serialize_charts(raw["charts"]),
    }
    return ok(data=BillingOverviewResponse.model_validate(payload))
