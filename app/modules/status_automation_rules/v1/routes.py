"""Status automation rules API (v1)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.common.deps import Allowed, AuthUser
from app.common.enums import PermissionLevel, Resource, UserRole
from app.common.exceptions import ValidationError
from app.common.response import ok
from app.common.schemas import PaginationParams, SuccessResponse
from app.core.rate_limit import (
    SUSPENSION_RULES_READ_RATE_LIMIT,
    SUSPENSION_RULES_WRITE_RATE_LIMIT,
    limiter,
)
from app.modules.status_automation_rules.enums import StatusAutomationRuleStatus, StatusAutomationScopeType
from app.modules.status_automation_rules.service import StatusAutomationRulesService
from app.modules.status_automation_rules.v1.schemas import (
    StatusAutomationRestoreDefaultRequest,
    StatusAutomationRuleSetCreateRequest,
    StatusAutomationRuleSetListResponse,
    StatusAutomationRuleSetResponse,
    StatusAutomationRuleSetUpdateRequest,
    StatusAutomationStatusUpdateRequest,
)

router = APIRouter()

ServiceDep = Annotated[StatusAutomationRulesService, Depends(StatusAutomationRulesService.dep)]
AdminWriteDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.SYSTEM_DEFAULTS, level=PermissionLevel.WRITE),
]
AdminReadDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.SYSTEM_DEFAULTS, level=PermissionLevel.READ),
]
OrgReadDep = Annotated[
    AuthUser,
    Allowed(
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
        UserRole.CUSTOMER_B2B,
        resource=Resource.SYSTEM_DEFAULTS,
        level=PermissionLevel.READ,
    ),
]
OrgWriteDep = Annotated[
    AuthUser,
    Allowed(
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
        UserRole.CUSTOMER_B2B,
        resource=Resource.SYSTEM_DEFAULTS,
        level=PermissionLevel.WRITE,
    ),
]


def _applies_to_label(item: Any) -> str:
    entity = item.trigger.entity_type.replace("_", " ").title() if item.trigger else "-"
    return entity


def _summary(item: Any) -> tuple[str, str, str]:
    trigger = item.trigger
    trigger_summary = "-"
    if trigger:
        trigger_summary = f"Status is {trigger.status_value.replace('_', ' ').title()}"
    cond_summary = " - " if not item.conditions else f"Timing is {item.conditions[0].value.replace('_', ' ').title()}"
    action_summary = " - " if not item.actions else f"Change status to {item.actions[0].new_status.replace('_', ' ').title()}"
    return trigger_summary, cond_summary, action_summary


def _to_response(item: Any, *, rule_kind: str, global_rule_set_id: str | None, is_effective_for_org: bool, can_restore_default: bool, can_delete: bool) -> StatusAutomationRuleSetResponse:
    trigger_summary, conditions_summary, actions_summary = _summary(item)
    return StatusAutomationRuleSetResponse(
        id=item.id,
        name=item.name,
        scope_type=StatusAutomationScopeType(item.scope_type),
        scope_org_id=item.scope_org_id,
        status=StatusAutomationRuleStatus(item.status),
        priority=item.priority,
        notes=item.notes,
        trigger={
            "entity_type": item.trigger.entity_type,
            "status": item.trigger.status_value,
        },
        conditions=[{"value": c.value} for c in item.conditions],
        actions=[{"new_status": a.new_status} for a in item.actions],
        created_at=item.created_at,
        updated_at=item.updated_at,
        version=item.version,
        rule_kind=rule_kind,
        global_rule_set_id=global_rule_set_id,
        is_effective_for_org=is_effective_for_org,
        can_restore_default=can_restore_default,
        applies_to_label=_applies_to_label(item),
        trigger_summary=trigger_summary,
        conditions_summary=conditions_summary,
        actions_summary=actions_summary,
        can_delete=can_delete,
        can_edit=True,
        can_toggle_status=True,
    )


@router.get("/rule-sets", response_model=SuccessResponse[StatusAutomationRuleSetListResponse])
@limiter.limit(SUSPENSION_RULES_READ_RATE_LIMIT)
async def list_rule_sets(
    request: Request,
    response: Response,
    service: ServiceDep,
    _user: AdminReadDep,
    pagination: PaginationParams = Depends(),  # noqa: B008
    scope_type: StatusAutomationScopeType | None = Query(default=None),  # noqa: B008
    scope_org_id: str | None = Query(default=None),  # noqa: B008
    status_: StatusAutomationRuleStatus | None = Query(default=None, alias="status"),  # noqa: B008
    q: str | None = Query(default=None),  # noqa: B008
    applies_to: str | None = Query(default=None),  # noqa: B008
    rule_kind: str | None = Query(default=None),  # noqa: B008
    sort_by: str = Query(default="updated_at"),  # noqa: B008
    sort_order: str = Query(default="desc"),  # noqa: B008
) -> dict:
    items, total = await service.list_rule_sets(
        scope_type=scope_type,
        scope_org_id=scope_org_id,
        status=status_,
        q=q,
        applies_to=applies_to,
        rule_kind=rule_kind,
        page=pagination.page,
        size=pagination.size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    out = []
    for item in items:
        kind = "DEFAULT" if item.scope_type == StatusAutomationScopeType.GLOBAL.value else ("CUSTOMISED" if item.parent_global_rule_set_id else "NEW")
        out.append(
            _to_response(
                item,
                rule_kind=kind,
                global_rule_set_id=item.parent_global_rule_set_id if kind == "CUSTOMISED" else (item.id if kind == "DEFAULT" else None),
                is_effective_for_org=False,
                can_restore_default=kind == "CUSTOMISED",
                can_delete=True,
            )
        )
    return ok(data=StatusAutomationRuleSetListResponse(items=out, total=total))


@router.get("/rule-sets/{rule_set_id}", response_model=SuccessResponse[StatusAutomationRuleSetResponse])
@limiter.limit(SUSPENSION_RULES_READ_RATE_LIMIT)
async def get_rule_set(request: Request, response: Response, rule_set_id: str, service: ServiceDep, _user: AdminReadDep) -> dict:
    item = await service.get_rule_set(rule_set_id)
    kind = "DEFAULT" if item.scope_type == StatusAutomationScopeType.GLOBAL.value else ("CUSTOMISED" if item.parent_global_rule_set_id else "NEW")
    return ok(
        data=_to_response(
            item,
            rule_kind=kind,
            global_rule_set_id=item.parent_global_rule_set_id if kind == "CUSTOMISED" else (item.id if kind == "DEFAULT" else None),
            is_effective_for_org=False,
            can_restore_default=kind == "CUSTOMISED",
            can_delete=True,
        )
    )


@router.post("/rule-sets", response_model=SuccessResponse[StatusAutomationRuleSetResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def create_rule_set(request: Request, response: Response, body: StatusAutomationRuleSetCreateRequest, service: ServiceDep, _user: AdminWriteDep) -> dict:
    item = await service.create_rule_set(
        payload={
            "name": body.name,
            "scope_type": body.scope_type.value,
            "scope_org_id": body.scope_org_id,
            "status": body.status.value,
            "priority": body.priority,
            "notes": body.notes,
        },
        trigger={
            "entity_type": body.trigger.entity_type.value,
            "status_value": body.trigger.status,
        },
        conditions=[{"value": c.value.value} for c in body.conditions],
        actions=[{"new_status": a.new_status} for a in body.actions],
    )
    kind = "DEFAULT" if item.scope_type == StatusAutomationScopeType.GLOBAL.value else ("CUSTOMISED" if item.parent_global_rule_set_id else "NEW")
    return ok(
        data=_to_response(
            item,
            rule_kind=kind,
            global_rule_set_id=item.parent_global_rule_set_id if kind == "CUSTOMISED" else (item.id if kind == "DEFAULT" else None),
            is_effective_for_org=False,
            can_restore_default=kind == "CUSTOMISED",
            can_delete=True,
        )
    )


@router.patch("/rule-sets/{rule_set_id}", response_model=SuccessResponse[StatusAutomationRuleSetResponse])
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def update_rule_set(
    request: Request,
    response: Response,
    rule_set_id: str,
    body: StatusAutomationRuleSetUpdateRequest,
    service: ServiceDep,
    _user: AdminWriteDep,
) -> dict:
    payload = {}
    for key in ("name", "status", "priority", "notes"):
        value = getattr(body, key)
        if value is not None:
            payload[key] = value.value if hasattr(value, "value") else value
    item = await service.update_rule_set(
        rule_set_id=rule_set_id,
        payload=payload,
        trigger=(
            {
                "entity_type": body.trigger.entity_type.value,
                "status_value": body.trigger.status,
            }
            if body.trigger is not None
            else None
        ),
        conditions=(
            [{"value": c.value.value} for c in body.conditions]
            if body.conditions is not None
            else None
        ),
        actions=([{"new_status": a.new_status} for a in body.actions] if body.actions is not None else None),
        expected_version=body.version,
    )
    kind = "DEFAULT" if item.scope_type == StatusAutomationScopeType.GLOBAL.value else ("CUSTOMISED" if item.parent_global_rule_set_id else "NEW")
    return ok(
        data=_to_response(
            item,
            rule_kind=kind,
            global_rule_set_id=item.parent_global_rule_set_id if kind == "CUSTOMISED" else (item.id if kind == "DEFAULT" else None),
            is_effective_for_org=False,
            can_restore_default=kind == "CUSTOMISED",
            can_delete=True,
        )
    )


@router.delete("/rule-sets/{rule_set_id}", response_model=SuccessResponse[dict])
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def delete_rule_set(request: Request, response: Response, rule_set_id: str, service: ServiceDep, _user: AdminWriteDep) -> dict:
    await service.delete_rule_set(rule_set_id=rule_set_id)
    return ok(data={})


@router.patch("/rule-sets/{rule_set_id}/status", response_model=SuccessResponse[StatusAutomationRuleSetResponse])
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def set_rule_status(
    request: Request,
    response: Response,
    rule_set_id: str,
    body: StatusAutomationStatusUpdateRequest,
    service: ServiceDep,
    _user: AdminWriteDep,
) -> dict:
    item = await service.set_rule_status(rule_set_id=rule_set_id, status=body.status, expected_version=body.version)
    kind = "DEFAULT" if item.scope_type == StatusAutomationScopeType.GLOBAL.value else ("CUSTOMISED" if item.parent_global_rule_set_id else "NEW")
    return ok(
        data=_to_response(
            item,
            rule_kind=kind,
            global_rule_set_id=item.parent_global_rule_set_id if kind == "CUSTOMISED" else (item.id if kind == "DEFAULT" else None),
            is_effective_for_org=False,
            can_restore_default=kind == "CUSTOMISED",
            can_delete=True,
        )
    )


@router.get("/orgs/{org_id}/applicable-rule-sets", response_model=SuccessResponse[StatusAutomationRuleSetListResponse])
@limiter.limit(SUSPENSION_RULES_READ_RATE_LIMIT)
async def list_applicable_for_org(
    request: Request,
    response: Response,
    org_id: str,
    service: ServiceDep,
    user: OrgReadDep,
) -> dict:
    service._assert_org_access(user.role, user.organization_id, org_id)
    rows = await service.get_applicable_for_org(org_id=org_id, include_inactive=True)
    items = [
        _to_response(
            row["rule_set"],
            rule_kind=row["rule_kind"],
            global_rule_set_id=row["global_rule_set_id"],
            is_effective_for_org=row["is_effective_for_org"],
            can_restore_default=row["can_restore_default"],
            can_delete=row["can_delete"],
        )
        for row in rows
    ]
    return ok(data=StatusAutomationRuleSetListResponse(items=items, total=len(items)))


@router.get("/orgs/{org_id}/effective-rule-sets", response_model=SuccessResponse[StatusAutomationRuleSetListResponse])
@limiter.limit(SUSPENSION_RULES_READ_RATE_LIMIT)
async def list_effective_for_org(
    request: Request,
    response: Response,
    org_id: str,
    service: ServiceDep,
    user: OrgReadDep,
) -> dict:
    service._assert_org_access(user.role, user.organization_id, org_id)
    rows = await service.get_effective_for_org(org_id=org_id)
    items = [
        _to_response(
            row["rule_set"],
            rule_kind=row["rule_kind"],
            global_rule_set_id=row["global_rule_set_id"],
            is_effective_for_org=True,
            can_restore_default=row["can_restore_default"],
            can_delete=row["can_delete"],
        )
        for row in rows
    ]
    return ok(data=StatusAutomationRuleSetListResponse(items=items, total=len(items)))


@router.post("/orgs/{org_id}/rule-sets/{global_rule_set_id}/customise", response_model=SuccessResponse[StatusAutomationRuleSetResponse])
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def customise_global_rule(
    request: Request,
    response: Response,
    org_id: str,
    global_rule_set_id: str,
    body: StatusAutomationRuleSetUpdateRequest,
    service: ServiceDep,
    user: OrgWriteDep,
) -> dict:
    service._assert_org_access(user.role, user.organization_id, org_id)
    payload = {}
    for key in ("name", "status", "priority", "notes"):
        value = getattr(body, key)
        if value is not None:
            payload[key] = value.value if hasattr(value, "value") else value
    item = await service.create_customised_from_global(
        org_id=org_id,
        global_rule_set_id=global_rule_set_id,
        payload=payload,
        trigger=(
            {
                "entity_type": body.trigger.entity_type.value,
                "status_value": body.trigger.status,
            }
            if body.trigger is not None
            else None
        ),
        conditions=(
            [{"value": c.value.value} for c in body.conditions]
            if body.conditions is not None
            else None
        ),
        actions=([{"new_status": a.new_status} for a in body.actions] if body.actions is not None else None),
    )
    return ok(
        data=_to_response(
            item,
            rule_kind="CUSTOMISED",
            global_rule_set_id=global_rule_set_id,
            is_effective_for_org=True,
            can_restore_default=True,
            can_delete=True,
        )
    )


@router.patch("/orgs/{org_id}/rule-sets/{rule_set_id}", response_model=SuccessResponse[StatusAutomationRuleSetResponse])
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def update_org_rule(
    request: Request,
    response: Response,
    org_id: str,
    rule_set_id: str,
    body: StatusAutomationRuleSetUpdateRequest,
    service: ServiceDep,
    user: OrgWriteDep,
) -> dict:
    service._assert_org_access(user.role, user.organization_id, org_id)
    row = await service.get_rule_set(rule_set_id)
    if row.scope_type != StatusAutomationScopeType.ORG.value or row.scope_org_id != org_id:
        raise ValidationError("Only ORG rule rows for this organization can be edited here.")
    payload = {}
    for key in ("name", "status", "priority", "notes"):
        value = getattr(body, key)
        if value is not None:
            payload[key] = value.value if hasattr(value, "value") else value
    item = await service.update_rule_set(
        rule_set_id=rule_set_id,
        payload=payload,
        trigger=(
            {
                "entity_type": body.trigger.entity_type.value,
                "status_value": body.trigger.status,
            }
            if body.trigger is not None
            else None
        ),
        conditions=(
            [{"value": c.value.value} for c in body.conditions]
            if body.conditions is not None
            else None
        ),
        actions=([{"new_status": a.new_status} for a in body.actions] if body.actions is not None else None),
        expected_version=body.version,
    )
    kind = "CUSTOMISED" if item.parent_global_rule_set_id else "NEW"
    return ok(
        data=_to_response(
            item,
            rule_kind=kind,
            global_rule_set_id=item.parent_global_rule_set_id,
            is_effective_for_org=True,
            can_restore_default=kind == "CUSTOMISED",
            can_delete=True,
        )
    )


@router.patch("/orgs/{org_id}/rule-sets/{rule_set_id}/status", response_model=SuccessResponse[StatusAutomationRuleSetResponse])
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def set_org_rule_status(
    request: Request,
    response: Response,
    org_id: str,
    rule_set_id: str,
    body: StatusAutomationStatusUpdateRequest,
    service: ServiceDep,
    user: OrgWriteDep,
) -> dict:
    service._assert_org_access(user.role, user.organization_id, org_id)
    row = await service.get_rule_set(rule_set_id)
    if row.scope_type != StatusAutomationScopeType.ORG.value or row.scope_org_id != org_id:
        raise ValidationError("Only ORG rule rows for this organization can be toggled here.")
    item = await service.set_rule_status(rule_set_id=rule_set_id, status=body.status, expected_version=body.version)
    kind = "CUSTOMISED" if item.parent_global_rule_set_id else "NEW"
    return ok(
        data=_to_response(
            item,
            rule_kind=kind,
            global_rule_set_id=item.parent_global_rule_set_id,
            is_effective_for_org=True,
            can_restore_default=kind == "CUSTOMISED",
            can_delete=True,
        )
    )


@router.post("/orgs/{org_id}/rule-sets/{rule_set_id}/restore-default", response_model=SuccessResponse[StatusAutomationRuleSetResponse])
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def restore_default(
    request: Request,
    response: Response,
    org_id: str,
    rule_set_id: str,
    body: StatusAutomationRestoreDefaultRequest,
    service: ServiceDep,
    user: OrgWriteDep,
) -> dict:
    service._assert_org_access(user.role, user.organization_id, org_id)
    item = await service.restore_default_for_customised(org_id=org_id, rule_set_id=rule_set_id, expected_version=body.version)
    return ok(
        data=_to_response(
            item,
            rule_kind="DEFAULT",
            global_rule_set_id=item.id,
            is_effective_for_org=True,
            can_restore_default=False,
            can_delete=False,
        )
    )


@router.delete("/orgs/{org_id}/rule-sets/{rule_set_id}", response_model=SuccessResponse[dict])
@limiter.limit(SUSPENSION_RULES_WRITE_RATE_LIMIT)
async def delete_org_rule(
    request: Request,
    response: Response,
    org_id: str,
    rule_set_id: str,
    service: ServiceDep,
    user: OrgWriteDep,
) -> dict:
    service._assert_org_access(user.role, user.organization_id, org_id)
    row = await service.get_rule_set(rule_set_id)
    if row.scope_type != StatusAutomationScopeType.ORG.value or row.scope_org_id != org_id:
        raise ValidationError("Only ORG rule rows for this organization can be deleted.")
    await service.delete_rule_set(rule_set_id=rule_set_id)
    return ok(data={})

