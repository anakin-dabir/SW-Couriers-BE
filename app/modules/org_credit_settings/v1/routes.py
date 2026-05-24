from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.deps import Allowed, AuthUser
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.response import ok
from app.common.schemas import PaginatedResponse, SuccessResponse
from app.modules.org_credit_settings.service import OrgCreditSettingsService
from app.modules.org_credit_settings.v1.docs import (
    GET_CREDIT_SETTINGS,
    GET_GLOBAL_CREDIT_COOLDOWN,
    GET_LIMIT_HISTORY,
    GET_ORG_ACTIVE_CREDIT_COOLDOWN,
    GET_ORG_CREDIT_COOLDOWN,
    GET_RISK_CONTROLS,
    GET_TERMS_HISTORY,
    PATCH_GLOBAL_CREDIT_COOLDOWN,
    PATCH_ORG_CREDIT_LIMIT,
    PATCH_ORG_PAYMENT_TERMS,
    PATCH_RISK_CONTROLS,
    POST_ORG_CREDIT_COOLDOWN,
)
from app.modules.org_credit_settings.v1.schemas import (
    ActiveCooldownResponse,
    CooldownPeriodResponse,
    CreditSettingsResponse,
    PatchGlobalCooldownRequest,
    PostOrgCooldownRequest,
    RiskControlsResponse,
    SetCreditLimitRequest,
    SetPaymentTermsRequest,
    SetRiskControlsRequest,
    TermsHistoryEntryResponse,
    TermsHistoryListParams,
    CreditLimitHistoryEntryResponse,
    CreditLimitHistoryListParams,
)
from app.modules.organizations.v1.routes import OrgProfileReadUserDep

org_credit_settings_router = APIRouter()

OrgCreditSettingsServiceDep = Annotated[OrgCreditSettingsService, Depends(OrgCreditSettingsService.dep)]

CreditAdminOrgReadDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.ORGANIZATIONS, level=PermissionLevel.READ),
]

CreditAdminOrgWriteDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.ORGANIZATIONS, level=PermissionLevel.WRITE),
]


@org_credit_settings_router.get(
    "/{org_id}/credit/settings",
    response_model=SuccessResponse[CreditSettingsResponse],
    **GET_CREDIT_SETTINGS,
)
async def get_credit_settings(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditSettingsServiceDep,
) -> dict:
    payload = await svc.get_settings_payload(org_id)
    return ok(payload)


@org_credit_settings_router.get(
    "/credit/settings/cooldown-period",
    response_model=SuccessResponse[CooldownPeriodResponse],
    **GET_GLOBAL_CREDIT_COOLDOWN,
)
async def get_global_credit_cooldown(
    _caller: CreditAdminOrgReadDep,
    svc: OrgCreditSettingsServiceDep,
) -> dict:
    payload = await svc.get_global_cooldown_payload()
    return ok(payload)


@org_credit_settings_router.patch(
    "/credit/settings/cooldown-period",
    response_model=SuccessResponse[CooldownPeriodResponse],
    **PATCH_GLOBAL_CREDIT_COOLDOWN,
)
async def patch_global_credit_cooldown(
    data: PatchGlobalCooldownRequest,
    caller: CreditAdminOrgWriteDep,
    svc: OrgCreditSettingsServiceDep,
) -> dict:
    payload = await svc.patch_global_cooldown(
        caller=caller,
        months=data.months,
        days=data.days,
        hours=data.hours,
        reset_to_defaults=data.reset_to_defaults,
    )
    return ok(payload, message="Global cool-down period updated.")


@org_credit_settings_router.get(
    "/{org_id}/credit/settings/active-cooldown",
    response_model=SuccessResponse[ActiveCooldownResponse],
    **GET_ORG_ACTIVE_CREDIT_COOLDOWN,
)
async def get_org_active_credit_cooldown(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditSettingsServiceDep,
) -> dict:
    payload = await svc.get_active_cooldown_public_payload(org_id)
    return ok(payload)


@org_credit_settings_router.get(
    "/{org_id}/credit/settings/risk-controls",
    response_model=SuccessResponse[RiskControlsResponse],
    **GET_RISK_CONTROLS,
)
async def get_org_risk_controls(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditSettingsServiceDep,
) -> dict:
    payload = await svc.get_risk_controls_payload(org_id)
    return ok(payload)


@org_credit_settings_router.patch(
    "/{org_id}/credit/settings/risk-controls",
    response_model=SuccessResponse,
    **PATCH_RISK_CONTROLS,
)
async def patch_org_risk_controls(
    org_id: str,
    data: SetRiskControlsRequest,
    caller: CreditAdminOrgWriteDep,
    svc: OrgCreditSettingsServiceDep,
) -> dict:
    await svc.patch_risk_controls(
        org_id,
        caller=caller,
        hold_threshold_pct=data.hold_threshold_pct,
    )
    return ok(message="Risk controls updated.")


@org_credit_settings_router.get(
    "/{org_id}/credit/settings/terms-history",
    response_model=SuccessResponse[PaginatedResponse[TermsHistoryEntryResponse]],
    **GET_TERMS_HISTORY,
)
async def get_terms_modification_history(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditSettingsServiceDep,
    params: Annotated[TermsHistoryListParams, Query()],
) -> dict:
    items, total = await svc.list_terms_modification_history(
        org_id,
        page=params.page,
        size=params.size,
    )
    response_items = [TermsHistoryEntryResponse.model_validate(i) for i in items]
    return ok(PaginatedResponse.create(response_items, total, params.page, params.size))


@org_credit_settings_router.get(
    "/{org_id}/credit/settings/cooldown-period",
    response_model=SuccessResponse[CooldownPeriodResponse],
    **GET_ORG_CREDIT_COOLDOWN,
)
async def get_org_credit_cooldown(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditSettingsServiceDep,
) -> dict:
    payload = await svc.get_org_cooldown_resolved_payload(org_id)
    return ok(payload)


@org_credit_settings_router.post(
    "/{org_id}/credit/settings/cooldown-period",
    response_model=SuccessResponse[CooldownPeriodResponse],
    **POST_ORG_CREDIT_COOLDOWN,
)
async def post_org_credit_cooldown(
    org_id: str,
    data: PostOrgCooldownRequest,
    caller: CreditAdminOrgWriteDep,
    svc: OrgCreditSettingsServiceDep,
) -> dict:
    payload = await svc.post_org_cooldown(
        org_id,
        caller=caller,
        months=data.months,
        days=data.days,
        hours=data.hours,
        reset_to_defaults=data.reset_to_defaults,
    )
    return ok(payload, message="Organisation cool-down period updated.")


@org_credit_settings_router.patch(
    "/{org_id}/credit/settings/adjust-limit",
    response_model=SuccessResponse,
    **PATCH_ORG_CREDIT_LIMIT,
)
async def patch_org_credit_limit(
    org_id: str,
    data: SetCreditLimitRequest,
    caller: CreditAdminOrgWriteDep,
    svc: OrgCreditSettingsServiceDep,
) -> dict:
    await svc.patch_credit_limit(
        org_id,
        caller=caller,
        credit_limit=data.credit_limit,
        reason_category=data.reason_category,
        effective_date=data.effective_date,
        justification=data.justification,
    )
    return ok(message="Credit limit updated.")


@org_credit_settings_router.get(
    "/{org_id}/credit/settings/limit-history",
    response_model=SuccessResponse[PaginatedResponse[CreditLimitHistoryEntryResponse]],
    **GET_LIMIT_HISTORY,
)
async def get_credit_limit_history(
    org_id: str,
    _caller: OrgProfileReadUserDep,
    svc: OrgCreditSettingsServiceDep,
    params: Annotated[CreditLimitHistoryListParams, Query()],
) -> dict:
    items, total = await svc.list_credit_limit_adjustment_history(
        org_id,
        page=params.page,
        size=params.size,
    )
    response_items = [CreditLimitHistoryEntryResponse.model_validate(i) for i in items]
    return ok(PaginatedResponse.create(response_items, total, params.page, params.size))


@org_credit_settings_router.patch(
    "/{org_id}/credit/settings/payment-terms",
    response_model=SuccessResponse,
    **PATCH_ORG_PAYMENT_TERMS,
)
async def patch_org_payment_terms(
    org_id: str,
    data: SetPaymentTermsRequest,
    caller: CreditAdminOrgWriteDep,
    svc: OrgCreditSettingsServiceDep,
) -> dict:
    await svc.patch_payment_terms(
        org_id,
        caller=caller,
        payment_terms_days=data.payment_terms_days,
        effective_date=data.effective_date,
        reason=data.reason,
        apply_to_existing_unpaid=data.apply_to_existing_unpaid,
    )
    return ok(message="Payment terms updated.")
