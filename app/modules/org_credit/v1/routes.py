from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.deps import Allowed, AuthUser
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.response import ok
from app.common.schemas import PaginatedResponse, SuccessResponse
from app.modules.org_credit.service import OrgCreditService
from app.modules.org_credit.v1.docs import (
    GET_CREDIT_ACTIVITY,
    GET_ORG_CREDIT_ACCOUNT_OVERVIEW,
    GET_ORG_CREDIT_OVERVIEW,
    GET_ORG_CREDIT_OVERVIEW_ACTIVE_ALERTS,
    GET_ORG_CREDIT_OVERVIEW_LIMIT_TREND,
    GET_ORG_CREDIT_OVERVIEW_UTILISATION_TREND,
    GET_ORG_CREDIT_STATUS_HISTORY,
    POST_ORG_CREDIT_CLOSE,
    POST_ORG_CREDIT_HOLD,
    POST_ORG_CREDIT_REACTIVATE,
    POST_ORG_CREDIT_RELEASE_HOLD,
    POST_ORG_CREDIT_SUSPEND,
)
from app.modules.org_credit.v1.schemas import (
    CloseAccountRequest,
    CreditAccountMutationResponse,
    CreditAccountOverviewResponse,
    CreditActivityEntryResponse,
    CreditActivityListParams,
    CreditOverviewResponse,
    CreditOverviewTrendQuery,
    PlaceHoldRequest,
    ReactivateAccountRequest,
    ReleaseHoldRequest,
    StatusHistoryEntryResponse,
    StatusHistoryListParams,
    SuspendAccountRequest,
)
from app.modules.org_credit_alerts.service import OrgCreditAlertService
from app.modules.org_credit_alerts.v1.schemas import AlertItem
from app.modules.org_credit_monitoring.service import OrgCreditMonitoringService
from app.modules.org_credit_monitoring.v1.schemas import TrendDataPoint
from app.modules.organizations.v1.routes import OrgProfileReadUserDep

router = APIRouter()

OrgCreditServiceDep = Annotated[OrgCreditService, Depends(OrgCreditService.dep)]
OrgCreditMonitoringServiceDep = Annotated[
    OrgCreditMonitoringService,
    Depends(OrgCreditMonitoringService.dep),
]
OrgCreditAlertServiceDep = Annotated[OrgCreditAlertService, Depends(OrgCreditAlertService.dep)]

CreditAdminWriteDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.ORGANIZATIONS, level=PermissionLevel.WRITE),
]


@router.get(
    "/{org_id}/credit/overview/limit-trend",
    response_model=SuccessResponse[list[TrendDataPoint]],
    **GET_ORG_CREDIT_OVERVIEW_LIMIT_TREND,
)
async def get_org_credit_overview_limit_trend(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditMonitoringServiceDep,
    trend: Annotated[CreditOverviewTrendQuery, Query()],
) -> dict:
    data = await svc.get_credit_limit_trend(
        org_id,
        year=trend.year,
        granularity=trend.granularity,
        month=trend.month,
    )
    return ok([TrendDataPoint.model_validate(d) for d in data])


@router.get(
    "/{org_id}/credit/overview/utilisation-trend",
    response_model=SuccessResponse[list[TrendDataPoint]],
    **GET_ORG_CREDIT_OVERVIEW_UTILISATION_TREND,
)
async def get_org_credit_overview_utilisation_trend(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditMonitoringServiceDep,
    trend: Annotated[CreditOverviewTrendQuery, Query()],
) -> dict:
    data = await svc.get_utilisation_trend(
        org_id,
        year=trend.year,
        granularity=trend.granularity,
        month=trend.month,
    )
    return ok([TrendDataPoint.model_validate(d) for d in data])


@router.get(
    "/{org_id}/credit/overview/active-alerts",
    response_model=SuccessResponse[list[AlertItem]],
    **GET_ORG_CREDIT_OVERVIEW_ACTIVE_ALERTS,
)
async def get_org_credit_overview_active_alerts(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditAlertServiceDep,
) -> dict:
    items = await svc.list_active_preview(org_id)
    return ok([AlertItem.model_validate(i) for i in items])


@router.get(
    "/{org_id}/credit/account-overview",
    response_model=SuccessResponse[CreditAccountOverviewResponse],
    **GET_ORG_CREDIT_ACCOUNT_OVERVIEW,
)
async def get_org_credit_account_overview(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditServiceDep,
) -> dict:
    payload = await svc.get_credit_account_overview(org_id)
    return ok(CreditAccountOverviewResponse.model_validate(payload))


@router.get(
    "/{org_id}/credit/overview",
    response_model=SuccessResponse[CreditOverviewResponse],
    **GET_ORG_CREDIT_OVERVIEW,
)
async def get_org_credit_overview(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditServiceDep,
) -> dict:
    payload = await svc.get_credit_overview(org_id)
    return ok(CreditOverviewResponse.model_validate(payload))


@router.get(
    "/{org_id}/credit/status-history",
    response_model=SuccessResponse[PaginatedResponse[StatusHistoryEntryResponse]],
    **GET_ORG_CREDIT_STATUS_HISTORY,
)
async def get_org_credit_status_history(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditServiceDep,
    params: Annotated[StatusHistoryListParams, Query()],
) -> dict:
    items, total = await svc.list_status_history(org_id, page=params.page, size=params.size)
    response_items = [StatusHistoryEntryResponse.model_validate(i) for i in items]
    return ok(PaginatedResponse.create(response_items, total, params.page, params.size))


@router.post(
    "/{org_id}/credit/hold",
    response_model=SuccessResponse[CreditAccountMutationResponse],
    **POST_ORG_CREDIT_HOLD,
)
async def post_org_credit_hold(
    org_id: str,
    data: PlaceHoldRequest,
    caller: CreditAdminWriteDep,
    svc: OrgCreditServiceDep,
) -> dict:
    acct = await svc.place_hold(
        org_id,
        caller=caller,
        hold_reason_category=data.hold_reason_category,
        detailed_reason=data.detailed_reason,
    )
    return ok(
        svc.credit_status_payload(acct),
        message="Credit account placed on hold.",
    )


@router.post(
    "/{org_id}/credit/hold/release",
    response_model=SuccessResponse[CreditAccountMutationResponse],
    **POST_ORG_CREDIT_RELEASE_HOLD,
)
async def post_org_credit_release_hold(
    org_id: str,
    data: ReleaseHoldRequest,
    caller: CreditAdminWriteDep,
    svc: OrgCreditServiceDep,
) -> dict:
    acct = await svc.release_hold(org_id, caller=caller, memo=data.memo)
    return ok(
        svc.credit_status_payload(acct),
        message="Hold released.",
    )


@router.post(
    "/{org_id}/credit/suspend",
    response_model=SuccessResponse[CreditAccountMutationResponse],
    **POST_ORG_CREDIT_SUSPEND,
)
async def post_org_credit_suspend(
    org_id: str,
    data: SuspendAccountRequest,
    caller: CreditAdminWriteDep,
    svc: OrgCreditServiceDep,
) -> dict:
    acct = await svc.suspend_account(
        org_id,
        caller=caller,
        reason=data.reason,
        trigger_payment_acceleration=data.trigger_payment_acceleration,
    )
    return ok(
        svc.credit_status_payload(acct),
        message="Credit account suspended.",
    )


@router.post(
    "/{org_id}/credit/reactivate",
    response_model=SuccessResponse[CreditAccountMutationResponse],
    **POST_ORG_CREDIT_REACTIVATE,
)
async def post_org_credit_reactivate(
    org_id: str,
    data: ReactivateAccountRequest,
    caller: CreditAdminWriteDep,
    svc: OrgCreditServiceDep,
) -> dict:
    acct = await svc.reactivate_account(org_id, caller=caller, memo=data.memo)
    return ok(
        svc.credit_status_payload(acct),
        message="Credit account reactivated.",
    )


@router.post(
    "/{org_id}/credit/close",
    response_model=SuccessResponse[CreditAccountMutationResponse],
    **POST_ORG_CREDIT_CLOSE,
)
async def post_org_credit_close(
    org_id: str,
    data: CloseAccountRequest,
    caller: CreditAdminWriteDep,
    svc: OrgCreditServiceDep,
) -> dict:
    acct = await svc.close_account(
        org_id,
        caller=caller,
        reason_category=data.reason_category,
        detailed_reason=data.detailed_reason,
    )
    return ok(
        svc.credit_status_payload(acct),
        message="Credit account closed.",
    )


@router.get(
    "/{org_id}/credit/activity",
    response_model=SuccessResponse[PaginatedResponse[CreditActivityEntryResponse]],
    **GET_CREDIT_ACTIVITY,
)
async def get_credit_activity(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditServiceDep,
    params: Annotated[CreditActivityListParams, Query()],
) -> dict:
    items, total = await svc.list_credit_activity(
        org_id,
        page=params.page,
        size=params.size,
        event_types=[e.value for e in params.event_type] if params.event_type else None,
        user_types=list(params.user_type) if params.user_type else None,
        severities=list(params.severity) if params.severity else None,
        search=params.search,
        from_date=params.from_date,
        to_date=params.to_date,
    )
    response_items = [CreditActivityEntryResponse.model_validate(i) for i in items]
    return ok(PaginatedResponse.create(response_items, total, params.page, params.size))
