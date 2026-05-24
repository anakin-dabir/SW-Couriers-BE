from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.common.deps import Allowed, AuthUser
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.response import ok
from app.common.schemas import PaginatedResponse, SuccessResponse
from app.modules.org_credit_alerts.service import OrgCreditAlertService
from app.modules.org_credit_alerts.v1.docs import (
    GET_CREDIT_ALERT_CONFIG,
    GET_CREDIT_ALERT_DETAIL,
    GET_CREDIT_ALERT_SUMMARY,
    GET_CREDIT_ALERTS_ACTIVE,
    GET_CREDIT_ALERTS_HISTORY,
    GET_GLOBAL_CREDIT_ALERT_THRESHOLDS,
    PATCH_CREDIT_ALERT_CONFIG,
    PATCH_GLOBAL_CREDIT_ALERT_THRESHOLDS,
    POST_CREDIT_ALERT_ACKNOWLEDGE,
    POST_CREDIT_ALERT_SNOOZE,
)
from app.modules.org_credit_alerts.v1.schemas import (
    AlertAcknowledgeRequest,
    AlertConfigListResponse,
    AlertConfigUpdateRequest,
    AlertHistoryParams,
    AlertItem,
    AlertSnoozeRequest,
    AlertSummaryResponse,
    GlobalThresholdListResponse,
    GlobalThresholdUpdateRequest,
)

router = APIRouter()

OrgCreditAlertServiceDep = Annotated[OrgCreditAlertService, Depends(OrgCreditAlertService.dep)]

CreditAlertsReadDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.ORGANIZATIONS, level=PermissionLevel.READ),
]
CreditAlertsAdminWriteDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.ORGANIZATIONS, level=PermissionLevel.WRITE),
]


@router.get(
    "/{org_id}/credit/alerts/summary",
    response_model=SuccessResponse[AlertSummaryResponse],
    **GET_CREDIT_ALERT_SUMMARY,
)
async def get_credit_alerts_summary(
    org_id: str,
    _caller: CreditAlertsReadDep,
    svc: OrgCreditAlertServiceDep,
) -> dict:
    payload = await svc.get_summary(org_id)
    return ok(payload)


@router.get(
    "/{org_id}/credit/alerts/active",
    response_model=SuccessResponse[list[AlertItem]],
    **GET_CREDIT_ALERTS_ACTIVE,
)
async def list_active_credit_alerts(
    org_id: str,
    _caller: CreditAlertsReadDep,
    svc: OrgCreditAlertServiceDep,
) -> dict:
    items = await svc.list_active(org_id)
    return ok([AlertItem.model_validate(i) for i in items])


@router.get(
    "/{org_id}/credit/alerts/history",
    response_model=SuccessResponse[PaginatedResponse[AlertItem]],
    **GET_CREDIT_ALERTS_HISTORY,
)
async def list_credit_alerts_history(
    request: Request,
    org_id: str,
    _caller: CreditAlertsReadDep,
    svc: OrgCreditAlertServiceDep,
    params: Annotated[AlertHistoryParams, Query()],
) -> dict:
    items, total = await svc.list_history(
        org_id,
        page=params.page,
        size=params.size,
        statuses=params.statuses,
        alert_types=params.alert_types,
    )
    response_items = [AlertItem.model_validate(i) for i in items]
    return ok(PaginatedResponse.create(items=response_items, total=total, page=params.page, size=params.size, request=request))


@router.get(
    "/{org_id}/credit/alerts/config",
    response_model=SuccessResponse[AlertConfigListResponse],
    **GET_CREDIT_ALERT_CONFIG,
)
async def get_credit_alert_config(
    org_id: str,
    _caller: CreditAlertsReadDep,
    svc: OrgCreditAlertServiceDep,
) -> dict:
    items = await svc.list_configs(org_id)
    return ok({"items": items})


@router.patch(
    "/{org_id}/credit/alerts/config",
    response_model=SuccessResponse[AlertConfigListResponse],
    **PATCH_CREDIT_ALERT_CONFIG,
)
async def patch_credit_alert_config(
    org_id: str,
    data: AlertConfigUpdateRequest,
    caller: CreditAlertsAdminWriteDep,
    svc: OrgCreditAlertServiceDep,
) -> dict:
    items = await svc.upsert_configs(
        org_id,
        caller=caller,
        items=[i.model_dump() for i in data.items],
    )
    return ok({"items": items}, message="Alert configuration updated.")


@router.get(
    "/{org_id}/credit/alerts/{alert_id}",
    response_model=SuccessResponse[AlertItem],
    **GET_CREDIT_ALERT_DETAIL,
)
async def get_credit_alert_detail(
    org_id: str,
    alert_id: str,
    _caller: CreditAlertsReadDep,
    svc: OrgCreditAlertServiceDep,
) -> dict:
    data = await svc.get_detail(org_id, alert_id)
    return ok(AlertItem.model_validate(data))


@router.post(
    "/{org_id}/credit/alerts/{alert_id}/acknowledge",
    response_model=SuccessResponse[AlertItem],
    **POST_CREDIT_ALERT_ACKNOWLEDGE,
)
async def acknowledge_credit_alert(
    org_id: str,
    alert_id: str,
    data: AlertAcknowledgeRequest,
    caller: CreditAlertsAdminWriteDep,
    svc: OrgCreditAlertServiceDep,
) -> dict:
    result = await svc.acknowledge(
        org_id,
        alert_id,
        caller=caller,
        resolution_notes=data.resolution_notes,
    )
    return ok(AlertItem.model_validate(result), message="Alert acknowledged.")


@router.post(
    "/{org_id}/credit/alerts/{alert_id}/snooze",
    response_model=SuccessResponse[AlertItem],
    **POST_CREDIT_ALERT_SNOOZE,
)
async def snooze_credit_alert(
    org_id: str,
    alert_id: str,
    data: AlertSnoozeRequest,
    caller: CreditAlertsAdminWriteDep,
    svc: OrgCreditAlertServiceDep,
) -> dict:
    result = await svc.snooze(
        org_id,
        alert_id,
        caller=caller,
        duration=data.duration,
    )
    return ok(AlertItem.model_validate(result), message="Alert snoozed.")


global_router = APIRouter()


@global_router.get(
    "/global-thresholds",
    response_model=SuccessResponse[GlobalThresholdListResponse],
    **GET_GLOBAL_CREDIT_ALERT_THRESHOLDS,
)
async def get_global_credit_alert_thresholds(
    _caller: CreditAlertsReadDep,
    svc: OrgCreditAlertServiceDep,
) -> dict:
    items = await svc.list_global_thresholds()
    return ok({"items": items})


@global_router.patch(
    "/global-thresholds",
    response_model=SuccessResponse[GlobalThresholdListResponse],
    **PATCH_GLOBAL_CREDIT_ALERT_THRESHOLDS,
)
async def patch_global_credit_alert_thresholds(
    data: GlobalThresholdUpdateRequest,
    caller: CreditAlertsAdminWriteDep,
    svc: OrgCreditAlertServiceDep,
) -> dict:
    items = await svc.update_global_thresholds(
        caller=caller,
        items=[i.model_dump() for i in data.items],
    )
    return ok({"items": items}, message="Global thresholds updated.")
