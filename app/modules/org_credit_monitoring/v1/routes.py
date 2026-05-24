from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.common.deps import Allowed, AuthUser
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.response import ok
from app.common.schemas import PaginatedResponse, SuccessResponse
from app.modules.org_credit_monitoring.service import OrgCreditMonitoringService
from app.modules.org_credit_monitoring.v1.docs import (
    GET_CREDIT_LIMIT_TREND,
    GET_CREDITSAFE_REPORT,
    GET_INTERNAL_SCORE,
    GET_INTERNAL_SCORE_TREND,
    GET_ORG_CREDIT_LEDGER,
    GET_UTILISATION,
    GET_UTILISATION_TREND,
    POST_CREDITSAFE_RECALCULATE,
    POST_INTERNAL_SCORE_RECALCULATE,
)
from app.modules.org_credit.v1.schemas import CreditReportResponse
from app.modules.org_credit_monitoring.v1.schemas import (
    CreditLedgerEntryResponse,
    CreditLedgerListParams,
    InternalScoreResponse,
    InternalScoreTrendDataPoint,
    RecalculateCreditSafeRequest,
    TrendDataPoint,
    UtilisationHistoryParams,
    UtilisationResponse,
)
from app.modules.organizations.v1.routes import OrgProfileReadUserDep

router = APIRouter()

OrgCreditMonitoringServiceDep = Annotated[OrgCreditMonitoringService, Depends(OrgCreditMonitoringService.dep)]

CreditAdminWriteDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.ORGANIZATIONS, level=PermissionLevel.WRITE),
]


@router.get(
    "/{org_id}/credit/monitoring/ledger",
    response_model=SuccessResponse[PaginatedResponse[CreditLedgerEntryResponse]],
    **GET_ORG_CREDIT_LEDGER,
)
async def list_org_credit_ledger(
    request: Request,
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditMonitoringServiceDep,
    params: Annotated[CreditLedgerListParams, Query()],
) -> dict:
    items, total = await svc.list_ledger(
        org_id, page=params.page, size=params.size, movement_type=params.movement_type,
    )
    response_items = [CreditLedgerEntryResponse.model_validate(svc.ledger_entry_to_dict(e)) for e in items]
    return ok(PaginatedResponse.create(items=response_items, total=total, page=params.page, size=params.size, request=request))


@router.get(
    "/{org_id}/credit/monitoring/creditsafe-report",
    response_model=SuccessResponse[CreditReportResponse],
    **GET_CREDITSAFE_REPORT,
)
async def get_creditsafe_report(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditMonitoringServiceDep,
) -> dict:
    return ok(await svc.get_creditsafe_report(org_id))


@router.post(
    "/{org_id}/credit/monitoring/creditsafe-recalculate",
    response_model=SuccessResponse[CreditReportResponse],
    **POST_CREDITSAFE_RECALCULATE,
)
async def post_creditsafe_recalculate(
    org_id: str,
    data: RecalculateCreditSafeRequest,
    caller: CreditAdminWriteDep,
    svc: OrgCreditMonitoringServiceDep,
) -> dict:
    result = await svc.recalculate_creditsafe(org_id, caller=caller)
    return ok(result, message="CreditSafe report recalculated.")


@router.get(
    "/{org_id}/credit/monitoring/internal-score",
    response_model=SuccessResponse[InternalScoreResponse],
    **GET_INTERNAL_SCORE,
)
async def get_internal_score(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditMonitoringServiceDep,
) -> dict:
    payload = await svc.get_internal_score(org_id)
    return ok(InternalScoreResponse.model_validate(payload))


@router.post(
    "/{org_id}/credit/monitoring/internal-score-recalculate",
    response_model=SuccessResponse[InternalScoreResponse],
    **POST_INTERNAL_SCORE_RECALCULATE,
)
async def post_internal_score_recalculate(
    org_id: str,
    caller: CreditAdminWriteDep,
    svc: OrgCreditMonitoringServiceDep,
) -> dict:
    payload = await svc.recalculate_internal_score(org_id, caller=caller)
    return ok(InternalScoreResponse.model_validate(payload), message="Internal score recalculated.")


@router.get(
    "/{org_id}/credit/monitoring/utilisation",
    response_model=SuccessResponse[UtilisationResponse],
    **GET_UTILISATION,
)
async def get_utilisation(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditMonitoringServiceDep,
    params: Annotated[UtilisationHistoryParams, Query()],
) -> dict:
    payload = await svc.get_utilisation(
        org_id,
        page=params.page,
        size=params.size,
        date_from=params.date_from,
        date_to=params.date_to,
    )
    return ok(UtilisationResponse.model_validate(payload))


@router.get(
    "/{org_id}/credit/monitoring/credit-limit-trend",
    response_model=SuccessResponse[list[TrendDataPoint]],
    **GET_CREDIT_LIMIT_TREND,
)
async def get_credit_limit_trend(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditMonitoringServiceDep,
    year: int = Query(ge=2020, le=2030),
    granularity: str = Query(default="monthly"),
    month: int | None = Query(default=None, ge=1, le=12),
) -> dict:
    data = await svc.get_credit_limit_trend(org_id, year=year, granularity=granularity, month=month)
    return ok([TrendDataPoint.model_validate(d) for d in data])


@router.get(
    "/{org_id}/credit/monitoring/utilisation-trend",
    response_model=SuccessResponse[list[TrendDataPoint]],
    **GET_UTILISATION_TREND,
)
async def get_utilisation_trend(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditMonitoringServiceDep,
    year: int = Query(ge=2020, le=2030),
    granularity: str = Query(default="monthly"),
    month: int | None = Query(default=None, ge=1, le=12),
) -> dict:
    data = await svc.get_utilisation_trend(org_id, year=year, granularity=granularity, month=month)
    return ok([TrendDataPoint.model_validate(d) for d in data])


@router.get(
    "/{org_id}/credit/monitoring/internal-score-trend",
    response_model=SuccessResponse[list[InternalScoreTrendDataPoint]],
    **GET_INTERNAL_SCORE_TREND,
)
async def get_internal_score_trend(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditMonitoringServiceDep,
    year: int = Query(ge=2020, le=2030),
    granularity: str = Query(default="monthly"),
) -> dict:
    data = await svc.get_internal_score_trend(org_id, year=year, granularity=granularity)
    return ok([InternalScoreTrendDataPoint.model_validate(d) for d in data])


