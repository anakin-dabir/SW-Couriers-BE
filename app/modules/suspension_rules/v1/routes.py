"""Suspension Rules admin API (v1 endpoints)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.common.deps import Allowed, AuthUser
from app.common.enums.user import UserRole
from app.common.response import ok
from app.common.schemas import PaginationParams, SuccessResponse
from app.core.rate_limit import (
    SUSPENSION_RULES_READ_RATE_LIMIT,
    SUSPENSION_RULES_WRITE_RATE_LIMIT,
    limiter,
)
from app.modules.organizations.enums import PaymentModel
from app.modules.suspension_rules.enums import RuleScopeType, SuspensionRuleStatus, SuspensionRuleType
from app.modules.suspension_rules.service import SuspensionRulesService, infer_org_override_response_meta
from app.modules.suspension_rules.v1.docs import (
    SUSP_V2_ACTIVITY_LIST,
    SUSP_V2_APPLICABLE_RULESETS,
    SUSP_V2_EFFECTIVE_RULESETS,
    SUSP_V2_ORG_CUSTOMISE_GLOBAL,
    SUSP_V2_ORG_RULE_RESTORE_DEFAULT,
    SUSP_V2_ORG_RULE_STATUS_UPDATE,
    SUSP_V2_ORG_GLOBAL_SUPPRESSION_LIST,
    SUSP_V2_ORG_GLOBAL_SUPPRESSION_PUT,
    SUSP_V2_ORG_OVERRIDE_UPSERT,
    SUSP_V2_RISK_EVENTS_CREATE,
    SUSP_V2_RULESETS_CREATE,
    SUSP_V2_RULESETS_DELETE,
    SUSP_V2_RULESETS_GET,
    SUSP_V2_RULESETS_LIST,
    SUSP_V2_RULESETS_UPDATE,
)
from app.modules.suspension_rules.v1.schemas import (
    OrgCustomiseGlobalRuleRequest,
    OrgGlobalSuppressionListResponse,
    OrgGlobalSuppressionPutRequest,
    OrgRuleOverrideUpsertRequest,
    OrgRuleRestoreDefaultRequest,
    OrgRuleStatusUpdateRequest,
    PaymentRiskEventCreateRequest,
    SuspensionActivityV2ListResponse,
    SuspensionActivityV2Response,
    SuspensionRuleSetCreateRequest,
    SuspensionRuleSetListResponse,
    SuspensionRuleSetResponse,
    SuspensionRuleSetUpdateRequest,
)

router = APIRouter()

SuspensionRulesServiceDep = Annotated[SuspensionRulesService, Depends(SuspensionRulesService.dep)]
SuspensionReadDep = Annotated[AuthUser, Allowed()]
SuspensionWriteDep = Annotated[AuthUser, Allowed()]


def _to_rule_set_response(
    item,
    *,
    is_override: bool = False,
    source_scope_type: RuleScopeType | None = None,
    source_rule_set_id: str | None = None,
    global_rule_set_id: str | None = None,
    rule_kind: str | None = None,
    is_effective_for_org: bool = False,
) -> SuspensionRuleSetResponse:
    resolved_kind = rule_kind
    if resolved_kind is None:
        if item.scope_type == RuleScopeType.GLOBAL.value:
            resolved_kind = "DEFAULT"
        elif getattr(item, "parent_global_rule_set_id", None):
            resolved_kind = "CUSTOMISED"
        else:
            resolved_kind = "NEW"
    resolved_global_rule_set_id = global_rule_set_id
    if resolved_global_rule_set_id is None and resolved_kind == "CUSTOMISED":
        resolved_global_rule_set_id = getattr(item, "parent_global_rule_set_id", None)
    return SuspensionRuleSetResponse(
        id=item.id,
        name=item.name,
        condition_summary=item.condition_summary,
        scope_type=RuleScopeType(item.scope_type),
        scope_org_id=item.scope_org_id,
        rule_type=SuspensionRuleType(item.rule_type),
        status=SuspensionRuleStatus(item.status),
        notes=item.notes,
        auto_suspension_enabled=item.auto_suspension_enabled,
        pause_new_bookings=item.pause_new_bookings,
        restrict_portal_login=item.restrict_portal_login,
        notify_finance_team=item.notify_finance_team,
        notify_account_manager=item.notify_account_manager,
        conditions=[
            {
                "position": cond.position,
                "connector": cond.connector,
                "condition_type": cond.condition_type,
                "threshold_value": float(cond.threshold_value),
                "unit": cond.unit,
            }
            for cond in sorted(item.conditions, key=lambda c: c.position)
        ],
        created_at=item.created_at,
        updated_at=item.updated_at,
        version=item.version,
        is_override=is_override,
        source_scope_type=source_scope_type,
        source_rule_set_id=source_rule_set_id,
        global_rule_set_id=resolved_global_rule_set_id,
        is_default_rule=resolved_kind == "DEFAULT",
        is_customised_rule=resolved_kind == "CUSTOMISED",
        is_new_rule=resolved_kind == "NEW",
        is_effective_for_org=is_effective_for_org,
        can_restore_default=bool(resolved_kind == "CUSTOMISED" and resolved_global_rule_set_id),
    )


@router.get(
    "/rule-sets",
    response_model=SuccessResponse[SuspensionRuleSetListResponse],
    **SUSP_V2_RULESETS_LIST,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_READ_RATE_LIMIT)
async def list_suspension_rule_sets(
    request: Request,
    response: Response,
    service: SuspensionRulesServiceDep,  # noqa: B008
    _user: SuspensionReadDep,
    pagination: PaginationParams = Depends(),  # noqa: B008
    scope_type: RuleScopeType | None = Query(default=None),  # noqa: B008
    scope_org_id: str | None = Query(default=None),  # noqa: B008
    rule_type: SuspensionRuleType | None = Query(default=None),  # noqa: B008
    status: SuspensionRuleStatus | None = Query(default=None),  # noqa: B008
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    items, total = await service.list_rule_sets(
        scope_type=scope_type,
        scope_org_id=scope_org_id,
        rule_type=rule_type,
        status=status,
        page=pagination.page,
        size=pagination.size,
    )
    responses = [_to_rule_set_response(item) for item in items]
    return ok(data=SuspensionRuleSetListResponse(items=responses, total=total))


@router.get(
    "/rule-sets/{rule_set_id}",
    response_model=SuccessResponse[SuspensionRuleSetResponse],
    **SUSP_V2_RULESETS_GET,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_READ_RATE_LIMIT)
async def get_suspension_rule_set(
    request: Request,
    response: Response,
    rule_set_id: str,
    service: SuspensionRulesServiceDep,
    _user: SuspensionReadDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    item = await service.get_rule_set(rule_set_id)
    return ok(
        data=_to_rule_set_response(item)
    )


@router.get(
    "/effective-rule-sets/{org_id}",
    response_model=SuccessResponse[SuspensionRuleSetListResponse],
    **SUSP_V2_EFFECTIVE_RULESETS,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_READ_RATE_LIMIT)
async def get_effective_suspension_rule_sets(
    request: Request,
    response: Response,
    org_id: str,
    service: SuspensionRulesServiceDep,  # noqa: B008
    _user: SuspensionReadDep,
    rule_type: SuspensionRuleType | None = Query(default=None),  # noqa: B008
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    items = await service.get_effective_rule_sets_with_source_for_org(org_id, rule_type=rule_type)
    responses = [
        _to_rule_set_response(
            item["rule_set"],
            is_override=bool(item.get("is_override")),
            source_scope_type=RuleScopeType(item["source_scope_type"]) if item.get("source_scope_type") else None,
            source_rule_set_id=item.get("source_rule_set_id"),
            global_rule_set_id=item.get("global_rule_set_id"),
            rule_kind=item.get("rule_kind"),
            is_effective_for_org=True,
        )
        for item in items
    ]
    return ok(data=SuspensionRuleSetListResponse(items=responses, total=len(responses)))


@router.get(
    "/orgs/{org_id}/applicable-rule-sets",
    response_model=SuccessResponse[SuspensionRuleSetListResponse],
    **SUSP_V2_APPLICABLE_RULESETS,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_READ_RATE_LIMIT)
async def get_applicable_suspension_rule_sets(
    request: Request,
    response: Response,
    org_id: str,
    service: SuspensionRulesServiceDep,  # noqa: B008
    _user: SuspensionReadDep,
    rule_type: SuspensionRuleType | None = Query(default=None),  # noqa: B008
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    items = await service.get_org_applicable_rule_sets_with_source_for_org(org_id, rule_type=rule_type)
    responses = [
        _to_rule_set_response(
            item["rule_set"],
            is_override=bool(item.get("is_override")),
            source_scope_type=RuleScopeType(item["source_scope_type"]) if item.get("source_scope_type") else None,
            source_rule_set_id=item.get("source_rule_set_id"),
            global_rule_set_id=item.get("global_rule_set_id"),
            rule_kind=item.get("rule_kind"),
            is_effective_for_org=bool(item.get("is_effective_for_org")),
        )
        for item in items
    ]
    return ok(data=SuspensionRuleSetListResponse(items=responses, total=len(responses)))


@router.post(
    "/rule-sets",
    response_model=SuccessResponse[SuspensionRuleSetResponse],
    status_code=status.HTTP_201_CREATED,
    **SUSP_V2_RULESETS_CREATE,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def create_suspension_rule_set(
    request: Request,
    response: Response,
    body: SuspensionRuleSetCreateRequest,
    service: SuspensionRulesServiceDep,
    _user: SuspensionWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    item = await service.create_rule_set(
        payload={
            "name": body.name,
            "condition_summary": body.condition_summary,
            "scope_type": body.scope_type.value,
            "scope_org_id": body.scope_org_id,
            "rule_type": body.rule_type.value,
            "status": body.status.value,
            "notes": body.notes,
            "auto_suspension_enabled": body.auto_suspension_enabled,
            "pause_new_bookings": body.pause_new_bookings,
            "restrict_portal_login": body.restrict_portal_login,
            "notify_finance_team": body.notify_finance_team,
            "notify_account_manager": body.notify_account_manager,
        },
        conditions=[
            {
                "position": cond.position,
                "connector": cond.connector.value if cond.connector else None,
                "condition_type": cond.condition_type.value,
                "threshold_value": cond.threshold_value,
                "unit": cond.unit,
            }
            for cond in body.conditions
        ],
        audit_user_id=_user.id,
        audit_user_role=_user.role,
    )
    return ok(
        data=_to_rule_set_response(item)
    )


@router.patch(
    "/rule-sets/{rule_set_id}",
    response_model=SuccessResponse[SuspensionRuleSetResponse],
    **SUSP_V2_RULESETS_UPDATE,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def update_suspension_rule_set(
    request: Request,
    response: Response,
    rule_set_id: str,
    body: SuspensionRuleSetUpdateRequest,
    service: SuspensionRulesServiceDep,
    _user: SuspensionWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    payload = {}
    for key in (
        "name",
        "condition_summary",
        "status",
        "notes",
        "auto_suspension_enabled",
        "pause_new_bookings",
        "restrict_portal_login",
        "notify_finance_team",
        "notify_account_manager",
    ):
        value = getattr(body, key)
        if value is not None:
            payload[key] = value.value if hasattr(value, "value") else value
    conditions = None
    if body.conditions is not None:
        conditions = [
            {
                "position": cond.position,
                "connector": cond.connector.value if cond.connector else None,
                "condition_type": cond.condition_type.value,
                "threshold_value": cond.threshold_value,
                "unit": cond.unit,
            }
            for cond in body.conditions
        ]
    item = await service.update_rule_set(
        rule_set_id=rule_set_id,
        payload=payload,
        conditions=conditions,
        expected_version=body.version,
        audit_user_id=_user.id,
        audit_user_role=_user.role,
    )
    return ok(
        data=_to_rule_set_response(item)
    )


@router.put(
    "/orgs/{org_id}/rule-types/{rule_type}/override",
    response_model=SuccessResponse[SuspensionRuleSetResponse],
    **SUSP_V2_ORG_OVERRIDE_UPSERT,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def upsert_org_rule_override(
    request: Request,
    response: Response,
    org_id: str,
    rule_type: SuspensionRuleType,
    body: OrgRuleOverrideUpsertRequest,
    service: SuspensionRulesServiceDep,
    _user: SuspensionWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    payload = {}
    for key in (
        "name",
        "condition_summary",
        "status",
        "notes",
        "auto_suspension_enabled",
        "pause_new_bookings",
        "restrict_portal_login",
        "notify_finance_team",
        "notify_account_manager",
    ):
        value = getattr(body, key)
        if value is not None:
            payload[key] = value.value if hasattr(value, "value") else value
    conditions = None
    if body.conditions is not None:
        conditions = [
            {
                "position": cond.position,
                "connector": cond.connector.value if cond.connector else None,
                "condition_type": cond.condition_type.value,
                "threshold_value": cond.threshold_value,
                "unit": cond.unit,
            }
            for cond in body.conditions
        ]
    item = await service.upsert_org_rule_override(
        organization_id=org_id,
        rule_type=rule_type,
        payload=payload,
        conditions=conditions,
        expected_version=body.version,
        audit_user_id=_user.id,
        audit_user_role=_user.role,
    )
    g_id, rule_kind = infer_org_override_response_meta(item)
    return ok(
        data=_to_rule_set_response(
            item,
            is_override=True,
            source_scope_type=RuleScopeType.ORG,
            source_rule_set_id=item.id,
            global_rule_set_id=g_id,
            rule_kind=rule_kind,
            is_effective_for_org=True,
        )
    )


@router.post(
    "/orgs/{org_id}/rule-sets/{global_rule_set_id}/customise",
    response_model=SuccessResponse[SuspensionRuleSetResponse],
    status_code=status.HTTP_201_CREATED,
    **SUSP_V2_ORG_CUSTOMISE_GLOBAL,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def create_customised_org_rule_from_global(
    request: Request,
    response: Response,
    org_id: str,
    global_rule_set_id: str,
    body: OrgCustomiseGlobalRuleRequest,
    service: SuspensionRulesServiceDep,
    _user: SuspensionWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    payload = {}
    for key in (
        "name",
        "condition_summary",
        "status",
        "notes",
        "auto_suspension_enabled",
        "pause_new_bookings",
        "restrict_portal_login",
        "notify_finance_team",
        "notify_account_manager",
    ):
        value = getattr(body, key)
        if value is not None:
            payload[key] = value.value if hasattr(value, "value") else value
    conditions = None
    if body.conditions is not None:
        conditions = [
            {
                "position": cond.position,
                "connector": cond.connector.value if cond.connector else None,
                "condition_type": cond.condition_type.value,
                "threshold_value": cond.threshold_value,
                "unit": cond.unit,
            }
            for cond in body.conditions
        ]
    item = await service.create_customised_rule_from_global(
        organization_id=org_id,
        global_rule_set_id=global_rule_set_id,
        payload=payload,
        conditions=conditions,
        audit_user_id=_user.id,
        audit_user_role=_user.role,
    )
    return ok(
        data=_to_rule_set_response(
            item,
            is_override=True,
            source_scope_type=RuleScopeType.ORG,
            source_rule_set_id=item.id,
            global_rule_set_id=global_rule_set_id,
            rule_kind="CUSTOMISED",
            is_effective_for_org=True,
        )
    )


@router.patch(
    "/orgs/{org_id}/rule-sets/{rule_set_id}/status",
    response_model=SuccessResponse[SuspensionRuleSetResponse],
    **SUSP_V2_ORG_RULE_STATUS_UPDATE,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def update_org_rule_status(
    request: Request,
    response: Response,
    org_id: str,
    rule_set_id: str,
    body: OrgRuleStatusUpdateRequest,
    service: SuspensionRulesServiceDep,
    _user: SuspensionWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    item = await service.set_org_rule_status(
        organization_id=org_id,
        rule_set_id=rule_set_id,
        status=body.status,
        expected_version=body.version,
        audit_user_id=_user.id,
        audit_user_role=_user.role,
    )
    return ok(data=_to_rule_set_response(item))


@router.post(
    "/orgs/{org_id}/rule-sets/{rule_set_id}/restore-default",
    response_model=SuccessResponse[SuspensionRuleSetResponse],
    **SUSP_V2_ORG_RULE_RESTORE_DEFAULT,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def restore_default_for_customised_org_rule(
    request: Request,
    response: Response,
    org_id: str,
    rule_set_id: str,
    body: OrgRuleRestoreDefaultRequest,
    service: SuspensionRulesServiceDep,
    _user: SuspensionWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    item = await service.restore_default_for_customised_rule(
        organization_id=org_id,
        rule_set_id=rule_set_id,
        expected_version=body.version,
        audit_user_id=_user.id,
        audit_user_role=_user.role,
    )
    return ok(
        data=_to_rule_set_response(
            item,
            is_override=False,
            source_scope_type=RuleScopeType.GLOBAL,
            source_rule_set_id=item.id,
            global_rule_set_id=item.id,
            rule_kind="DEFAULT",
            is_effective_for_org=True,
        )
    )


@router.get(
    "/orgs/{org_id}/global-rule-suppressions",
    response_model=SuccessResponse[OrgGlobalSuppressionListResponse],
    **SUSP_V2_ORG_GLOBAL_SUPPRESSION_LIST,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_READ_RATE_LIMIT)
async def list_org_global_suppressions_api(
    request: Request,
    response: Response,
    org_id: str,
    service: SuspensionRulesServiceDep,
    _user: SuspensionReadDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    ids = await service.list_org_global_suppressions(org_id)
    return ok(data=OrgGlobalSuppressionListResponse(global_rule_set_ids=ids))


@router.put(
    "/orgs/{org_id}/global-rule-sets/{global_rule_set_id}/suppression",
    response_model=SuccessResponse[OrgGlobalSuppressionListResponse],
    **SUSP_V2_ORG_GLOBAL_SUPPRESSION_PUT,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def set_org_global_suppression_api(
    request: Request,
    response: Response,
    org_id: str,
    global_rule_set_id: str,
    body: OrgGlobalSuppressionPutRequest,
    service: SuspensionRulesServiceDep,
    _user: SuspensionWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    await service.set_org_global_suppression(
        organization_id=org_id,
        global_rule_set_id=global_rule_set_id,
        suppressed=body.suppressed,
        audit_user_id=_user.id,
        audit_user_role=_user.role,
    )
    ids = await service.list_org_global_suppressions(org_id)
    return ok(data=OrgGlobalSuppressionListResponse(global_rule_set_ids=ids))


@router.delete(
    "/rule-sets/{rule_set_id}",
    response_model=SuccessResponse[dict],
    **SUSP_V2_RULESETS_DELETE,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def delete_suspension_rule_set(
    request: Request,
    response: Response,
    rule_set_id: str,
    service: SuspensionRulesServiceDep,
    _user: SuspensionWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    await service.delete_rule_set(rule_set_id=rule_set_id, audit_user_id=_user.id, audit_user_role=_user.role)
    return ok(data={})


@router.post(
    "/risk-events",
    response_model=SuccessResponse[dict],
    status_code=status.HTTP_201_CREATED,
    **SUSP_V2_RISK_EVENTS_CREATE,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def create_payment_risk_event(
    request: Request,
    response: Response,
    body: PaymentRiskEventCreateRequest,
    service: SuspensionRulesServiceDep,
    _user: SuspensionWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    event = await service.create_payment_risk_event(
        organization_id=body.organization_id,
        customer_id=body.customer_id,
        order_id=body.order_id,
        payment_model=PaymentModel(body.payment_model),
        event_type=body.event_type,
        occurred_on=body.occurred_on.date() if body.occurred_on else None,
        metadata=body.metadata,
    )
    return ok(data={"id": event.id})


@router.get(
    "/activity",
    response_model=SuccessResponse[SuspensionActivityV2ListResponse],
    **SUSP_V2_ACTIVITY_LIST,  # type: ignore[arg-type]
)
@limiter.limit(SUSPENSION_RULES_READ_RATE_LIMIT)
async def list_suspension_activity(
    request: Request,
    response: Response,
    service: SuspensionRulesServiceDep,  # noqa: B008
    _user: SuspensionReadDep,
    pagination: PaginationParams = Depends(),  # noqa: B008
    account_id: str | None = Query(default=None),  # noqa: B008
    rule_set_id: str | None = Query(default=None, description="Canonical rule set id filter."),  # noqa: B008
    rule_id: str | None = Query(default=None, deprecated=True, description="Deprecated alias of rule_set_id."),  # noqa: B008
    organization_id: str | None = Query(default=None),  # noqa: B008
    rule_type: str | None = Query(default=None),  # noqa: B008
    payment_model: str | None = Query(default=None),  # noqa: B008
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden")
    items, total = await service.list_activity_v2(
        account_id=account_id,
        rule_set_id=rule_set_id,
        rule_id=rule_id,
        organization_id=organization_id,
        rule_type=rule_type,
        payment_model=payment_model,
        page=pagination.page,
        size=pagination.size,
    )
    return ok(data=SuspensionActivityV2ListResponse(items=[SuspensionActivityV2Response(**it) for it in items], total=total))
