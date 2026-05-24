"""QuickBooks integration API routes."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.common.deps import Allowed, AuthUser, IdempotencyKeyDep
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.exceptions import ValidationError
from app.common.response import ok
from app.common.schemas import MessageResponse, SuccessResponse
from app.common.oauth_redirect import build_oauth_redirect, validate_oauth_redirect_url
from app.core.config import settings
from app.core.rate_limit import (
    QUICKBOOKS_CALLBACK_DUPLICATE_RATE_LIMIT,
    QUICKBOOKS_CALLBACK_RATE_LIMIT,
    QUICKBOOKS_RECONCILE_RATE_LIMIT,
    QUICKBOOKS_RESYNC_RATE_LIMIT,
    QUICKBOOKS_SYNC_RATE_LIMIT,
    limiter,
)
from app.integrations.quickbooks.docs import (
    QB_CALLBACK,
    QB_CONNECT_URL,
    QB_DISCONNECT,
    QB_FAILURE_DETAIL,
    QB_FAILURES_LIST,
    QB_MAPPINGS_DEACTIVATE,
    QB_MAPPINGS_LIST,
    QB_MAPPINGS_UPSERT,
    QB_RECONCILE,
    QB_RESYNC_BULK,
    QB_RESYNC_ENTITY,
    QB_RESYNC_FINAL_FAILURES,
    QB_SETTINGS_GET,
    QB_SETTINGS_UPDATE,
    QB_STATUS,
    QB_SYNC_CREDIT_NOTE,
    QB_SYNC_CUSTOMER,
    QB_SYNC_HEALTH,
    QB_SYNC_INVOICE,
    QB_SYNC_PAYMENT,
    QB_VALIDATE_INVOICE,
)
from app.integrations.quickbooks.schemas import (
    QuickBooksBulkResyncRequest,
    QuickBooksBulkResyncResponse,
    QuickBooksCallbackResponse,
    QuickBooksConnectUrlResponse,
    QuickBooksEntityType,
    QuickBooksFailureLogDetailResponse,
    QuickBooksFailureLogListItem,
    QuickBooksFailureLogsListResponse,
    QuickBooksFailuresListQuery,
    QuickBooksFinalFailuresResyncRequest,
    QuickBooksLogAction,
    QuickBooksLogEventType,
    QuickBooksLogStatus,
    QuickBooksMappingResponse,
    QuickBooksMappingsListResponse,
    QuickBooksMappingUpsertRequest,
    QuickBooksPreflightResult,
    QuickBooksReconcileResponse,
    QuickBooksResyncEntityType,
    QuickBooksResyncRequest,
    QuickBooksStatusResponse,
    QuickBooksSyncHealthResponse,
    QuickBooksSyncRequest,
    QuickBooksSyncResult,
    QuickBooksSyncSettingsResponse,
    QuickBooksSyncSettingsUpdateRequest,
)
from app.integrations.quickbooks.service import QuickBooksService
from app.modules.orders.enums import SummaryPeriodPreset

router = APIRouter()
logger = structlog.get_logger()

QuickBooksReadDep = Annotated[
    AuthUser,
    Allowed(UserRole.SUPER_ADMIN, UserRole.ADMIN, resource=Resource.QUICKBOOKS, level=PermissionLevel.READ),
]
QuickBooksWriteDep = Annotated[
    AuthUser,
    Allowed(UserRole.SUPER_ADMIN, UserRole.ADMIN, resource=Resource.QUICKBOOKS, level=PermissionLevel.WRITE),
]
QuickBooksServiceDep = Annotated[QuickBooksService, Depends(QuickBooksService.dep)]


def _quickbooks_callback_limit_key(request: Request) -> str:
    """Rate-key for OAuth callback duplicate checks: client IP + state hash."""
    client_ip = request.client.host if request.client else "unknown"
    if settings.TRUST_X_FORWARDED_FOR:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip() or client_ip
    state = (request.query_params.get("state") or "").strip()
    state_hash = "missing-state"
    if state:
        state_hash = f"state:{hashlib.sha256(state.encode('utf-8')).hexdigest()[:16]}"
    return f"{client_ip}:{state_hash}"


def _log_qb_admin_action(*, action: str, user: AuthUser, scope_id: str) -> None:
    logger.info(
        "quickbooks.admin_action",
        action=action,
        actor_user_id=user.id,
        actor_role=user.role,
        effective_scope_id=scope_id,
    )


@router.get(
    "/connect-url",
    response_model=SuccessResponse[QuickBooksConnectUrlResponse],
    **QB_CONNECT_URL,
)
async def get_connect_url(
    user: QuickBooksReadDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    _log_qb_admin_action(action="connect_url_requested", user=user, scope_id=org_id)
    data = await service.get_connect_url(organization_id=org_id, actor_user_id=user.id)
    return ok(data=QuickBooksConnectUrlResponse(**data))


@router.get(
    "/callback",
    response_model=SuccessResponse[QuickBooksCallbackResponse],
    **QB_CALLBACK,
)
@limiter.limit(QUICKBOOKS_CALLBACK_RATE_LIMIT)
@limiter.limit(QUICKBOOKS_CALLBACK_DUPLICATE_RATE_LIMIT, key_func=_quickbooks_callback_limit_key)
async def oauth_callback(
    request: Request,
    response: Response,
    service: QuickBooksServiceDep,
    state: str = Query(..., min_length=1, max_length=512),
    code: str = Query(..., min_length=1, max_length=4096),
    realm_id: str = Query(..., alias="realmId", min_length=1, max_length=64),
    format: str | None = Query(default=None, alias="format"),
):
    if not state.strip() or not code.strip() or not realm_id.strip():
        raise ValidationError("OAuth callback parameters are invalid")
    try:
        data = await service.handle_callback(state=state, code=code, realm_id=realm_id)
    except Exception:
        error_url = (settings.QUICKBOOKS_OAUTH_ERROR_URL or "").strip()
        if error_url and format != "json":
            target = build_oauth_redirect(error_url, query={"status": "error"})
            return RedirectResponse(url=target, status_code=302)
        raise
    if format == "json" or not (settings.QUICKBOOKS_OAUTH_SUCCESS_URL or "").strip():
        return ok(data=QuickBooksCallbackResponse(**data), message="QuickBooks connected successfully")
    success_base = validate_oauth_redirect_url(settings.QUICKBOOKS_OAUTH_SUCCESS_URL, field_name="QUICKBOOKS_OAUTH_SUCCESS_URL")
    target = build_oauth_redirect(
        success_base,
        query={"status": "connected", "connected": "1", "realm_id": str(data.get("realm_id") or realm_id)},
    )
    return RedirectResponse(url=target, status_code=302)


@router.get(
    "/status",
    response_model=SuccessResponse[QuickBooksStatusResponse],
    **QB_STATUS,
)
async def get_status(
    user: QuickBooksReadDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    data = await service.get_status(organization_id=org_id)
    return ok(data=QuickBooksStatusResponse(**data))


@router.post(
    "/disconnect",
    response_model=SuccessResponse[MessageResponse],
    **QB_DISCONNECT,
)
async def disconnect(
    user: QuickBooksWriteDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    _log_qb_admin_action(action="disconnect_requested", user=user, scope_id=org_id)
    await service.disconnect(organization_id=org_id)
    return ok(data=MessageResponse(message="QuickBooks disconnected"))


@router.post(
    "/customers/{customer_id}/sync",
    response_model=SuccessResponse[QuickBooksSyncResult],
    **QB_SYNC_CUSTOMER,
)
@limiter.limit(QUICKBOOKS_SYNC_RATE_LIMIT)
async def sync_customer(
    request: Request,
    response: Response,
    customer_id: str,
    body: QuickBooksSyncRequest,
    user: QuickBooksWriteDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    data = await service.enqueue_customer_sync(organization_id=org_id, customer_id=customer_id, force=body.force)
    return ok(data=QuickBooksSyncResult(**data))


@router.post(
    "/invoices/{invoice_id}/sync",
    response_model=SuccessResponse[QuickBooksSyncResult],
    **QB_SYNC_INVOICE,
)
@limiter.limit(QUICKBOOKS_SYNC_RATE_LIMIT)
async def sync_invoice(
    request: Request,
    response: Response,
    invoice_id: str,
    body: QuickBooksSyncRequest,
    user: QuickBooksWriteDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    data = await service.enqueue_invoice_sync(organization_id=org_id, invoice_id=invoice_id, force=body.force)
    return ok(data=QuickBooksSyncResult(**data))


@router.post(
    "/credit-notes/{credit_note_id}/sync",
    response_model=SuccessResponse[QuickBooksSyncResult],
    **QB_SYNC_CREDIT_NOTE,
)
@limiter.limit(QUICKBOOKS_SYNC_RATE_LIMIT)
async def sync_credit_note(
    request: Request,
    response: Response,
    credit_note_id: str,
    body: QuickBooksSyncRequest,
    user: QuickBooksWriteDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    data = await service.enqueue_credit_note_sync(
        organization_id=org_id,
        credit_note_id=credit_note_id,
        force=body.force,
    )
    return ok(data=QuickBooksSyncResult(**data))


@router.post(
    "/payments/{payment_id}/sync",
    response_model=SuccessResponse[QuickBooksSyncResult],
    **QB_SYNC_PAYMENT,
)
@limiter.limit(QUICKBOOKS_SYNC_RATE_LIMIT)
async def sync_payment(
    request: Request,
    response: Response,
    payment_id: str,
    body: QuickBooksSyncRequest,
    user: QuickBooksWriteDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    data = await service.enqueue_payment_sync(organization_id=org_id, payment_id=payment_id, force=body.force)
    return ok(data=QuickBooksSyncResult(**data))


@router.get(
    "/mappings",
    response_model=SuccessResponse[QuickBooksMappingsListResponse],
    **QB_MAPPINGS_LIST,
)
async def list_mappings(
    user: QuickBooksReadDep,
    service: QuickBooksServiceDep,
    mapping_type: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    org_id = service.resolve_swc_scope_id()
    data = await service.list_mappings(
        organization_id=org_id,
        mapping_type=mapping_type.upper() if mapping_type else None,
        is_active=is_active,
        limit=limit,
    )
    return ok(data=QuickBooksMappingsListResponse(items=[QuickBooksMappingResponse(**row) for row in data]))


@router.put(
    "/mappings/{mapping_type}/{local_key}",
    response_model=SuccessResponse[QuickBooksMappingResponse],
    **QB_MAPPINGS_UPSERT,
)
async def upsert_mapping(
    mapping_type: str,
    local_key: str,
    body: QuickBooksMappingUpsertRequest,
    user: QuickBooksWriteDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    _log_qb_admin_action(action="mapping_upsert_requested", user=user, scope_id=org_id)
    data = await service.upsert_mapping(
        organization_id=org_id,
        mapping_type=mapping_type,
        local_key=local_key,
        qb_ref_id=body.qb_ref_id,
        qb_ref_name=body.qb_ref_name,
        is_active=body.is_active,
        metadata=body.metadata,
    )
    return ok(data=QuickBooksMappingResponse(**data))


@router.delete(
    "/mappings/{mapping_type}/{local_key}",
    response_model=SuccessResponse[MessageResponse],
    **QB_MAPPINGS_DEACTIVATE,
)
async def deactivate_mapping(
    mapping_type: str,
    local_key: str,
    user: QuickBooksWriteDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    _log_qb_admin_action(action="mapping_deactivate_requested", user=user, scope_id=org_id)
    found = await service.delete_mapping(organization_id=org_id, mapping_type=mapping_type, local_key=local_key)
    if not found:
        return ok(data=MessageResponse(message="Mapping not found"))
    return ok(data=MessageResponse(message="Mapping deactivated"))


@router.get(
    "/settings",
    response_model=SuccessResponse[QuickBooksSyncSettingsResponse],
    **QB_SETTINGS_GET,
)
async def get_sync_settings(
    user: QuickBooksReadDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    _log_qb_admin_action(action="settings_read_requested", user=user, scope_id=org_id)
    data = await service.get_sync_settings(organization_id=org_id)
    return ok(data=QuickBooksSyncSettingsResponse(**data))


@router.patch(
    "/settings",
    response_model=SuccessResponse[QuickBooksSyncSettingsResponse],
    **QB_SETTINGS_UPDATE,
)
async def update_sync_settings(
    body: QuickBooksSyncSettingsUpdateRequest,
    user: QuickBooksWriteDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    _log_qb_admin_action(action="settings_update_requested", user=user, scope_id=org_id)
    data = await service.update_sync_settings(
        organization_id=org_id,
        updates=body.model_dump(),
    )
    return ok(data=QuickBooksSyncSettingsResponse(**data))


@router.post(
    "/validate/invoices/{invoice_id}",
    response_model=SuccessResponse[QuickBooksPreflightResult],
    **QB_VALIDATE_INVOICE,
)
async def validate_invoice(
    invoice_id: str,
    user: QuickBooksReadDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    data = await service.preflight_invoice_sync(organization_id=org_id, invoice_id=invoice_id)
    return ok(data=QuickBooksPreflightResult(**data))


@router.get(
    "/sync-health",
    response_model=SuccessResponse[QuickBooksSyncHealthResponse],
    **QB_SYNC_HEALTH,
)
async def sync_health(
    user: QuickBooksReadDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    data = await service.get_sync_health(organization_id=org_id)
    return ok(data=QuickBooksSyncHealthResponse(**data))


@router.get(
    "/reconcile",
    response_model=SuccessResponse[QuickBooksReconcileResponse],
    **QB_RECONCILE,
)
@limiter.limit(QUICKBOOKS_RECONCILE_RATE_LIMIT)
async def reconcile(
    request: Request,
    response: Response,
    user: QuickBooksReadDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    data = await service.reconcile(organization_id=org_id)
    return ok(data=QuickBooksReconcileResponse(**data))


@router.post(
    "/resync/{entity_type}/{local_entity_id}",
    response_model=SuccessResponse[QuickBooksSyncResult],
    **QB_RESYNC_ENTITY,
)
@limiter.limit(QUICKBOOKS_RESYNC_RATE_LIMIT)
async def resync_entity(
    request: Request,
    response: Response,
    entity_type: QuickBooksResyncEntityType,
    local_entity_id: str,
    body: QuickBooksResyncRequest,
    user: QuickBooksWriteDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    _log_qb_admin_action(action="resync_entity_requested", user=user, scope_id=org_id)
    data = await service.enqueue_resync(
        organization_id=org_id,
        entity_type=entity_type.value,
        local_entity_id=local_entity_id,
        force=body.force,
    )
    return ok(data=QuickBooksSyncResult(**data))


@router.get(
    "/failures",
    response_model=SuccessResponse[QuickBooksFailureLogsListResponse],
    **QB_FAILURES_LIST,
)
async def list_failures(
    user: QuickBooksReadDep,
    service: QuickBooksServiceDep,
    status: Annotated[list[QuickBooksLogStatus] | None, Query()] = None,
    entity_type: Annotated[QuickBooksEntityType | None, Query()] = None,
    event_type: Annotated[QuickBooksLogEventType | None, Query()] = None,
    action: Annotated[QuickBooksLogAction | None, Query()] = None,
    error_code: Annotated[str | None, Query()] = None,
    job_id: Annotated[str | None, Query()] = None,
    local_entity_id: Annotated[str | None, Query()] = None,
    search: Annotated[
        str | None,
        Query(
        description=(
            "Free-text filter across Job ID, Entity Type, QuickBooks ID, and Error Code. "
            "Case-insensitive partial match."
        ),
        min_length=1,
        max_length=100,
    ),
    ] = None,
    period: Annotated[
        SummaryPeriodPreset | None,
        Query(
            description=(
                "Preset created_at filter (e.g. LAST_7_DAYS). Mutually exclusive with date_from/date_to."
            ),
        ),
    ] = None,
    date_from: Annotated[
        date | None,
        Query(description="Inclusive UTC calendar start date; requires date_to when period is omitted."),
    ] = None,
    date_to: Annotated[
        date | None,
        Query(description="Inclusive UTC calendar end date; cannot be in the future."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    _ = QuickBooksFailuresListQuery(
        status=status,
        entity_type=entity_type,
        event_type=event_type,
        action=action,
        error_code=error_code,
        job_id=job_id,
        local_entity_id=local_entity_id,
        search=search,
        period=period,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    org_id = service.resolve_swc_scope_id()
    items = await service.list_logs(
        organization_id=org_id,
        statuses=status,
        entity_type=entity_type.value if entity_type is not None else None,
        event_type=event_type,
        action=action,
        error_code=error_code,
        job_id=job_id,
        local_entity_id=local_entity_id,
        search=search,
        period=period.value if period is not None else None,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    return ok(data=QuickBooksFailureLogsListResponse(items=[QuickBooksFailureLogListItem(**row) for row in items]))


@router.get(
    "/failures/{log_id}",
    response_model=SuccessResponse[QuickBooksFailureLogDetailResponse],
    **QB_FAILURE_DETAIL,
)
async def get_failure_detail(
    log_id: str,
    user: QuickBooksReadDep,
    service: QuickBooksServiceDep,
) -> dict:
    org_id = service.resolve_swc_scope_id()
    data = await service.get_log_detail(organization_id=org_id, log_id=log_id)
    return ok(data=QuickBooksFailureLogDetailResponse(**data))


@router.post(
    "/resync/bulk",
    response_model=SuccessResponse[QuickBooksBulkResyncResponse],
    **QB_RESYNC_BULK,
)
@limiter.limit(QUICKBOOKS_RESYNC_RATE_LIMIT)
async def bulk_resync(
    request: Request,
    response: Response,
    body: QuickBooksBulkResyncRequest,
    idempotency_key: IdempotencyKeyDep,
    user: QuickBooksWriteDep,
    service: QuickBooksServiceDep,
) -> dict:
    _ = idempotency_key
    org_id = service.resolve_swc_scope_id()
    _log_qb_admin_action(action="bulk_resync_requested", user=user, scope_id=org_id)
    data = await service.bulk_resync(
        organization_id=org_id,
        status=body.status,
        statuses=body.statuses,
        entity_type=body.entity_type.value if body.entity_type is not None else None,
        event_type=body.event_type,
        action=body.action,
        error_code=body.error_code,
        include_non_connection_failures=body.include_non_connection_failures,
        force=body.force,
        batch_size=body.batch_size,
        limit=body.limit,
    )
    return ok(data=QuickBooksBulkResyncResponse(**data))


@router.post(
    "/resync/final-failures",
    response_model=SuccessResponse[QuickBooksBulkResyncResponse],
    **QB_RESYNC_FINAL_FAILURES,
)
@limiter.limit(QUICKBOOKS_RESYNC_RATE_LIMIT)
async def final_failures_resync(
    request: Request,
    response: Response,
    body: QuickBooksFinalFailuresResyncRequest,
    idempotency_key: IdempotencyKeyDep,
    user: QuickBooksWriteDep,
    service: QuickBooksServiceDep,
) -> dict:
    _ = idempotency_key
    org_id = service.resolve_swc_scope_id()
    _log_qb_admin_action(action="final_failures_resync_requested", user=user, scope_id=org_id)
    data = await service.bulk_resync_final_failures(
        organization_id=org_id,
        entity_type=body.entity_type.value if body.entity_type is not None else None,
        event_type=body.event_type,
        action=body.action,
        error_code=body.error_code,
        force=body.force,
        batch_size=body.batch_size,
        limit=body.limit,
    )
    return ok(data=QuickBooksBulkResyncResponse(**data))
