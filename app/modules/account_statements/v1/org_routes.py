"""Admin account statement routes under /organizations/{org_id}/account-statements."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app.common.deps import Allowed, AuthUser
from app.common.enums import UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.response import ok
from app.common.schemas import PaginatedResponse, SuccessResponse
from app.core.rate_limit import DRIVERS_READ_RATE_LIMIT, DRIVERS_WRITE_RATE_LIMIT, limiter
from app.modules.account_statements.enums import StatementCreatedByType
from app.modules.account_statements.service import AccountStatementService, resolve_admin_org_id
from app.modules.account_statements.v1.docs import (
    ACCOUNT_STATEMENTS_CREATE,
    ACCOUNT_STATEMENTS_DELETE,
    ACCOUNT_STATEMENTS_GET,
    ACCOUNT_STATEMENTS_LIST,
    ACCOUNT_STATEMENTS_PDF_STATUS,
    ACCOUNT_STATEMENTS_PREVIEW,
    ACCOUNT_STATEMENTS_SCHEDULES_CREATE,
    ACCOUNT_STATEMENTS_SCHEDULES_LIST,
    ACCOUNT_STATEMENTS_SEND_EMAIL,
    ACCOUNT_STATEMENTS_SIGNED_URL,
    ACCOUNT_STATEMENTS_SUMMARY,
)
from app.modules.account_statements.v1.schemas import (
    StatementCreateRequest,
    StatementDetailResponse,
    StatementListItem,
    StatementPdfStatusResponse,
    StatementPreviewRequest,
    StatementPreviewResponse,
    StatementScheduleCreateRequest,
    StatementScheduleResponse,
    schedule_to_response,
    StatementSendEmailRequest,
    StatementSendEmailResponse,
    StatementSignedUrlRequest,
    StatementSignedUrlResponse,
    StatementAgingBuckets,
    StatementSummaryResponse,
    statement_to_detail,
    statement_to_list_item,
)

router = APIRouter()

StatementServiceDep = Annotated[AccountStatementService, Depends(AccountStatementService.dep)]
StatementReadDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.BILLING, level=PermissionLevel.READ),
]
StatementWriteDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.BILLING, level=PermissionLevel.WRITE),
]


@router.get(
    "/{org_id}/account-statements",
    response_model=SuccessResponse[PaginatedResponse[StatementListItem]],
    **ACCOUNT_STATEMENTS_LIST,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_statements(
    request: Request,
    response: Response,
    org_id: str,
    service: StatementServiceDep,
    user: StatementReadDep,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: str | None = None,
    period_start_from: date | None = None,
    period_start_to: date | None = None,
    generated_from: datetime | None = None,
    generated_to: datetime | None = None,
) -> dict:
    organization_id = resolve_admin_org_id(user, org_id)
    items, total = await service.list_statements(
        organization_id,
        page=page,
        size=size,
        search=search,
        period_start_from=period_start_from,
        period_start_to=period_start_to,
        generated_from=generated_from,
        generated_to=generated_to,
    )
    data = PaginatedResponse.create(
        [statement_to_list_item(s) for s in items],
        total=total,
        page=page,
        size=size,
        request=request,
    )
    return ok(data=data)


@router.get(
    "/{org_id}/account-statements/preview",
    response_model=SuccessResponse[StatementPreviewResponse],
    **ACCOUNT_STATEMENTS_PREVIEW,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def preview_statement(
    request: Request,
    response: Response,
    org_id: str,
    service: StatementServiceDep,
    user: StatementReadDep,
    period_start: date = Query(..., description="Statement period start (inclusive), ISO date."),
    period_end: date = Query(..., description="Statement period end (inclusive), ISO date. Cannot be in the future."),
    include_line_item_detail: bool = Query(
        False,
        description="Include invoice line_items[] on each INVOICE row in ledger.rows.",
    ),
    include_credit_notes: bool = Query(True, description="Include CREDIT_NOTE rows in the ledger."),
    include_payment_history: bool = Query(
        True,
        description="Include PAYMENT and REFUND rows in the ledger.",
    ),
) -> dict:
    organization_id = resolve_admin_org_id(user, org_id)
    data = await service.get_preview(
        organization_id=organization_id,
        period_start=period_start,
        period_end=period_end,
        include_line_item_detail=include_line_item_detail,
        include_credit_notes=include_credit_notes,
        include_payment_history=include_payment_history,
        preview_aging_as_of_today=True,
    )
    return ok(data=StatementPreviewResponse(**data))


@router.get(
    "/{org_id}/account-statements/summary",
    response_model=SuccessResponse[StatementSummaryResponse],
    **ACCOUNT_STATEMENTS_SUMMARY,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def statement_summary(
    request: Request,
    response: Response,
    org_id: str,
    service: StatementServiceDep,
    user: StatementReadDep,
    period_start: date = Query(...),
    period_end: date = Query(...),
    include_line_item_detail: bool = False,
    include_credit_notes: bool = True,
    include_payment_history: bool = True,
) -> dict:
    organization_id = resolve_admin_org_id(user, org_id)
    ledger = await service.build_ledger(
        organization_id=organization_id,
        period_start=period_start,
        period_end=period_end,
        include_line_item_detail=include_line_item_detail,
        include_credit_notes=include_credit_notes,
        include_payment_history=include_payment_history,
        aging_as_of=period_end,
    )
    return ok(
        data=StatementSummaryResponse(
            opening_balance=str(ledger.opening_balance),
            closing_balance=str(ledger.closing_balance),
            total_invoice_amount=str(ledger.total_invoice_amount),
            total_paid=str(ledger.total_paid),
            total_unpaid=str(ledger.total_unpaid),
            total_overdue=str(ledger.total_overdue),
            aging=StatementAgingBuckets.from_aging_dict(ledger.aging),
            currency=ledger.currency,
            truncated=ledger.truncated,
        )
    )


@router.post(
    "/{org_id}/account-statements",
    response_model=SuccessResponse[StatementDetailResponse],
    status_code=status.HTTP_201_CREATED,
    **ACCOUNT_STATEMENTS_CREATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def create_statement(
    request: Request,
    response: Response,
    org_id: str,
    body: StatementCreateRequest,
    service: StatementServiceDep,
    user: StatementWriteDep,
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> dict:
    organization_id = resolve_admin_org_id(user, org_id)
    stmt = await service.create_statement(
        organization_id=organization_id,
        period_start=body.period_start,
        period_end=body.period_end,
        include_line_item_detail=body.include_line_item_detail,
        include_credit_notes=body.include_credit_notes,
        include_payment_history=body.include_payment_history,
        created_by_user_id=user.id,
        created_by_user_type=StatementCreatedByType.ADMIN,
        idempotency_key=x_idempotency_key,
    )
    org = await service._ensure_org(organization_id)
    return ok(data=statement_to_detail(stmt, org=org))


@router.get(
    "/{org_id}/account-statements/{statement_id}",
    response_model=SuccessResponse[StatementDetailResponse],
    **ACCOUNT_STATEMENTS_GET,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_statement(
    request: Request,
    response: Response,
    org_id: str,
    statement_id: str,
    service: StatementServiceDep,
    user: StatementReadDep,
) -> dict:
    organization_id = resolve_admin_org_id(user, org_id)
    detail = await service.get_statement_detail(statement_id, organization_id=organization_id)
    return ok(data=detail)


@router.get(
    "/{org_id}/account-statements/{statement_id}/pdf/status",
    response_model=SuccessResponse[StatementPdfStatusResponse],
    **ACCOUNT_STATEMENTS_PDF_STATUS,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def pdf_status(
    request: Request,
    response: Response,
    org_id: str,
    statement_id: str,
    service: StatementServiceDep,
    user: StatementReadDep,
) -> dict:
    organization_id = resolve_admin_org_id(user, org_id)
    payload = await service.get_pdf_status(statement_id, organization_id=organization_id)
    return ok(data=StatementPdfStatusResponse(**payload))


@router.post(
    "/{org_id}/account-statements/{statement_id}/pdf/signed-url",
    response_model=SuccessResponse[StatementSignedUrlResponse],
    **ACCOUNT_STATEMENTS_SIGNED_URL,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def pdf_signed_url(
    request: Request,
    response: Response,
    org_id: str,
    statement_id: str,
    body: StatementSignedUrlRequest,
    service: StatementServiceDep,
    user: StatementReadDep,
) -> dict:
    organization_id = resolve_admin_org_id(user, org_id)
    url, expires_at = await service.get_signed_url(
        statement_id,
        organization_id=organization_id,
        disposition=body.disposition,
    )
    return ok(
        data=StatementSignedUrlResponse(
            url=url,
            expires_at=expires_at.isoformat(),
            disposition=body.disposition,
        )
    )


@router.post(
    "/{org_id}/account-statements/{statement_id}/send-email",
    response_model=SuccessResponse[StatementSendEmailResponse],
    **ACCOUNT_STATEMENTS_SEND_EMAIL,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def send_email(
    request: Request,
    response: Response,
    org_id: str,
    statement_id: str,
    body: StatementSendEmailRequest,
    service: StatementServiceDep,
    user: StatementWriteDep,
) -> dict:
    organization_id = resolve_admin_org_id(user, org_id)
    result = await service.send_email(
        statement_id,
        organization_id=organization_id,
        recipient_email=str(body.recipient_email),
        sent_by_user_id=user.id,
    )
    return ok(data=StatementSendEmailResponse(**result))


@router.delete(
    "/{org_id}/account-statements/{statement_id}",
    response_model=SuccessResponse[dict],
    **ACCOUNT_STATEMENTS_DELETE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_statement(
    request: Request,
    response: Response,
    org_id: str,
    statement_id: str,
    service: StatementServiceDep,
    user: StatementWriteDep,
) -> dict:
    organization_id = resolve_admin_org_id(user, org_id)
    await service.delete_statement(statement_id, organization_id=organization_id)
    return ok(data={"id": statement_id, "deleted": True})


@router.get(
    "/{org_id}/account-statement-schedules",
    response_model=SuccessResponse[list[StatementScheduleResponse]],
    **ACCOUNT_STATEMENTS_SCHEDULES_LIST,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_schedules(
    request: Request,
    response: Response,
    org_id: str,
    service: StatementServiceDep,
    user: StatementReadDep,
) -> dict:
    organization_id = resolve_admin_org_id(user, org_id)
    schedules = await service.list_schedules(organization_id)
    return ok(data=[schedule_to_response(s) for s in schedules])


@router.post(
    "/{org_id}/account-statement-schedules",
    response_model=SuccessResponse[StatementScheduleResponse],
    status_code=status.HTTP_201_CREATED,
    **ACCOUNT_STATEMENTS_SCHEDULES_CREATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def create_schedule(
    request: Request,
    response: Response,
    org_id: str,
    body: StatementScheduleCreateRequest,
    service: StatementServiceDep,
    user: StatementWriteDep,
) -> dict:
    organization_id = resolve_admin_org_id(user, org_id)
    schedule = await service.create_schedule(
        organization_id=organization_id,
        frequency=body.frequency,
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        recipient_email=str(body.recipient_email),
        timezone=body.timezone,
        interval_days=body.interval_days,
        include_line_item_detail=body.include_line_item_detail,
        include_credit_notes=body.include_credit_notes,
        include_payment_history=body.include_payment_history,
    )
    return ok(data=schedule_to_response(schedule))
