from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from pydantic import ValidationError as PydanticValidationError

from app.common.deps import Allowed, AllowedPaymentAccess, AuditCtxDep, AuthUser
from app.common.enums import ClientType, UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.exceptions import NotFoundError, ValidationError
from app.common.response import ok
from app.common.schemas import PaginatedResponse, SuccessResponse
from app.modules.billing.enums import (
    AllocationStatus,
    PaymentProvider,
    PaymentRecordStatus,
    RefundMethod,
    RefundReasonCategory,
    RefundStatus,
    RefundType,
)
from app.modules.billing.record_payment_client_type import parse_record_payment_client_type
from app.modules.billing.service import BillingService, parse_b2b_credit_note_customer_filter
from app.modules.billing.v1.docs import (
    BILLING_ADMIN_CREDIT_NOTES_APPLY,
    BILLING_ADMIN_CREDIT_NOTES_CANDIDATES,
    BILLING_ADMIN_CREDIT_NOTES_CLIENT_EMAIL,
    BILLING_ADMIN_CREDIT_NOTES_CREATE,
    BILLING_ADMIN_CREDIT_NOTES_GET,
    BILLING_ADMIN_CREDIT_NOTES_LIST,
    BILLING_ADMIN_CREDIT_NOTES_SEND,
    BILLING_ADMIN_CREDIT_NOTES_VOID,
    BILLING_B2B_CREDIT_NOTES_APPLY,
    BILLING_B2B_CREDIT_NOTES_CANDIDATES,
    BILLING_B2B_CREDIT_NOTES_GET,
    BILLING_B2B_CREDIT_NOTES_LIST,
    BILLING_B2B_CREDIT_NOTES_PDF_REQUEST,
    BILLING_B2B_CREDIT_NOTES_PDF_SIGNED_URL,
    BILLING_B2B_CREDIT_NOTES_PDF_STATUS,
    BILLING_PAYMENTS_ALLOCATE,
    BILLING_PAYMENTS_ALLOCATIONS_REPLACE,
    BILLING_PAYMENTS_ALLOCATIONS_REMOVE,
    BILLING_PAYMENTS_CREATE,
    BILLING_PAYMENTS_CREATE_MULTIPART,
    BILLING_PAYMENTS_GET,
    BILLING_PAYMENTS_INVOICE_CANDIDATES,
    BILLING_PAYMENTS_KPIS,
    BILLING_PAYMENTS_LIST,
    BILLING_PAYMENTS_OPTIONS,
    BILLING_PAYMENTS_NOTES_PATCH,
    BILLING_PAYMENTS_VOID,
    BILLING_PAYMENTS_REMITTANCE_DELETE,
    BILLING_PAYMENTS_REMITTANCE_PUT,
    BILLING_PAYMENTS_REMITTANCE_SIGNED_URL,
    BILLING_REFUNDS_CREATE,
    BILLING_REFUNDS_GET,
    BILLING_REFUNDS_KPIS,
    BILLING_REFUNDS_LIST,
    BILLING_REFUNDS_MARK_COMPLETE,
    BILLING_REFUNDS_OPTIONS,
    BILLING_REFUNDS_RETRY,
    BILLING_REFUNDS_ISSUE_CREDIT_NOTE,
)
from app.modules.billing.v1.schemas import (
    BillingPaymentAllocationBulkRequest,
    BillingPaymentAllocationReplaceRequest,
    BillingPaymentAllocationUpsertRequest,
    BillingPaymentCreateRequest,
    BillingPaymentDetailResponse,
    BillingPaymentInvoiceCandidateItem,
    BillingPaymentKpisResponse,
    BillingPaymentListItem,
    PaymentFilterOptionsResponse,
    PaymentHistoryListQuery,
    BillingPaymentNotesPatchRequest,
    BillingPaymentVoidRequest,
    BillingRemittanceSignedUrlResponse,
    RefundActionRequest,
    RefundCreateRequest,
    RefundDetailResponse,
    RefundDetailWithEventsResponse,
    RefundFilterOptionsResponse,
    RefundKpisResponse,
    RefundListItem,
    CreditNoteApplyRequest,
    CreditNoteApplyResponse,
    CreditNoteClientEmailResponse,
    CreditNoteCreateRequest,
    CreditNoteDetailResponse,
    CreditNoteInvoiceCandidateItem,
    CreditNoteListItem,
    CreditNotePdfSignedUrlRequest,
    CreditNotePdfSignedUrlResponse,
    CreditNotePdfStatusResponse,
    CreditNoteSendRequest,
    CreditNoteVoidRequest,
    credit_note_to_detail,
    credit_note_to_list_item,
    refund_to_detail,
    refund_to_list_item,
    payment_to_detail,
    payment_to_list_item,
)
from app.storage.upload import validate_remittance_advice

from app.modules.account_statements.v1.b2b_routes import router as account_statements_b2b_router

router = APIRouter()
router.include_router(account_statements_b2b_router)

BillingServiceDep = Annotated[BillingService, Depends(BillingService.dep)]
PaymentsAccessReadDep = Annotated[AuthUser, AllowedPaymentAccess(PermissionLevel.READ)]
PaymentsAccessWriteDep = Annotated[AuthUser, AllowedPaymentAccess(PermissionLevel.WRITE)]
RefundAdminReadDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.BILLING, level=PermissionLevel.READ),
]
RefundAdminWriteDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.BILLING, level=PermissionLevel.WRITE),
]
CreditNotesAdminReadDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.CREDIT_NOTES, level=PermissionLevel.READ),
]
CreditNotesAdminWriteDep = Annotated[
    AuthUser,
    Allowed(UserRole.ADMIN, UserRole.SUPER_ADMIN, resource=Resource.CREDIT_NOTES, level=PermissionLevel.WRITE),
]
RefundB2BReadDep = Annotated[AuthUser, Allowed(UserRole.CUSTOMER_B2B, resource=Resource.BILLING, level=PermissionLevel.READ)]
RefundB2BWriteDep = Annotated[AuthUser, Allowed(UserRole.CUSTOMER_B2B, resource=Resource.BILLING, level=PermissionLevel.WRITE)]


def _validate_org_id(raw: str, field_name: str = "organization_id") -> str:
    value = (raw or "").strip()
    if not value:
        raise ValidationError(f"{field_name} is required for this billing operation")
    try:
        UUID(value)
    except ValueError:
        raise ValidationError(
            f"{field_name} must be a valid UUID",
            details=[{"field": field_name, "message": "Must be a valid UUID", "type": "value_error.uuid"}],
        ) from None
    return value


def _org_scope(user: AuthUser, organization_id: str | None = None) -> str:
    """Resolve organisation id for tenant-scoped billing mutations and reads.

    - **CUSTOMER_B2B** is always restricted to ``user.organization_id``.
    - **ADMIN** / **SUPER_ADMIN** must pass ``organization_id`` explicitly (query/body).
      We do **not** fall back to ``user.organization_id`` for platform admins so the
      admin UI cannot accidentally scope to the wrong tenant or confuse payer validation.
    - Other roles use ``user.organization_id`` when set.
    """
    if user.client_type == ClientType.CUSTOMER_B2B and user.organization_id:
        return _validate_org_id(user.organization_id)

    role_val = user.role.value if isinstance(user.role, UserRole) else str(user.role)
    if role_val in (UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value):
        oid = (organization_id or "").strip()
        if not oid:
            raise ValidationError(
                "organization_id is required for this billing operation",
                details=[
                    {
                        "field": "organization_id",
                        "message": (
                            "ADMIN and SUPER_ADMIN must pass organisation_id (e.g. query parameter) for each "
                            "tenant-scoped billing call — same organisation as the chosen client / invoices."
                        ),
                        "type": "value_error",
                    }
                ],
            )
        return _validate_org_id(oid)

    if user.organization_id:
        return _validate_org_id(user.organization_id)
    raise ValidationError(
        "organization_id is required for this billing operation",
        details=[
            {
                "field": "organization_id",
                "message": "organisation_id is required for this billing operation",
                "type": "value_error",
            }
        ],
    )


def _billing_payment_read_org_scope(user: AuthUser, organization_id: str | None = None) -> str | None:
    """Org filter for payment history list and KPI aggregates.

    B2B callers are always restricted to their organisation.

    ADMIN and SUPER_ADMIN may omit ``organization_id`` for tenant-wide (global) aggregates.
    """
    if user.client_type == ClientType.CUSTOMER_B2B and user.organization_id:
        return _validate_org_id(user.organization_id)
    if user.role in (
        UserRole.ADMIN,
        UserRole.ADMIN.value,
        UserRole.SUPER_ADMIN,
        UserRole.SUPER_ADMIN.value,
    ):
        oid = (organization_id or "").strip()
        return _validate_org_id(oid) if oid else None
    if user.organization_id:
        return _validate_org_id(user.organization_id)
    raise ValidationError("organization_id is required for this billing operation")


@router.get(
    "/payments/history",
    response_model=SuccessResponse[PaginatedResponse[BillingPaymentListItem]],
    **BILLING_PAYMENTS_LIST,
)
async def list_payment_history(
    request: Request,
    response: Response,
    service: BillingServiceDep,
    user: PaymentsAccessReadDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    organization_id: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    payment_date_from: Annotated[date | None, Query()] = None,
    payment_date_to: Annotated[date | None, Query()] = None,
    status: Annotated[list[PaymentRecordStatus] | None, Query()] = None,
    allocation_status: Annotated[list[AllocationStatus] | None, Query()] = None,
    provider: Annotated[list[PaymentProvider] | None, Query()] = None,
) -> dict:
    _ = response
    filters = PaymentHistoryListQuery(
        status=status,
        allocation_status=allocation_status,
        provider=provider,
        payment_date_from=payment_date_from,
        payment_date_to=payment_date_to,
        search=search,
    )
    org_id = _billing_payment_read_org_scope(user, organization_id)
    items, total = await service.list_payment_history(
        organization_id=org_id,
        page=page,
        size=size,
        search=filters.search,
        payment_date_from=filters.payment_date_from,
        payment_date_to=filters.payment_date_to,
        status=[s.value for s in (filters.status or [])] or None,
        allocation_status=[s.value for s in (filters.allocation_status or [])] or None,
        provider=[s.value for s in (filters.provider or [])] or None,
    )
    allocation_map = await service.payment_allocation_summaries([p.id for p in items])
    for item in items:
        item._allocations = allocation_map.get(item.id, [])
    data = PaginatedResponse.create([payment_to_list_item(p) for p in items], total=total, page=page, size=size, request=request)
    return ok(data=data)


@router.get(
    "/payments/kpis",
    response_model=SuccessResponse[BillingPaymentKpisResponse],
    **BILLING_PAYMENTS_KPIS,
)
async def payment_kpis(
    service: BillingServiceDep,
    user: PaymentsAccessReadDep,
    organization_id: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    payment_date_from: Annotated[date | None, Query()] = None,
    payment_date_to: Annotated[date | None, Query()] = None,
    status: Annotated[list[PaymentRecordStatus] | None, Query()] = None,
    allocation_status: Annotated[list[AllocationStatus] | None, Query()] = None,
    provider: Annotated[list[PaymentProvider] | None, Query()] = None,
) -> dict:
    filters = PaymentHistoryListQuery(
        status=status,
        allocation_status=allocation_status,
        provider=provider,
        payment_date_from=payment_date_from,
        payment_date_to=payment_date_to,
        search=search,
    )
    org_id = _billing_payment_read_org_scope(user, organization_id)
    kpis = await service.payment_kpis(
        organization_id=org_id,
        search=filters.search,
        payment_date_from=filters.payment_date_from,
        payment_date_to=filters.payment_date_to,
        status=[s.value for s in (filters.status or [])] or None,
        allocation_status=[s.value for s in (filters.allocation_status or [])] or None,
        provider=[s.value for s in (filters.provider or [])] or None,
    )
    return ok(
        data=BillingPaymentKpisResponse(
            total_received=kpis["total_received"],
            allocated=kpis["allocated"],
            unallocated=kpis["unallocated"],
            pending=kpis["pending"],
        )
    )


@router.get(
    "/payments/options",
    response_model=SuccessResponse[PaymentFilterOptionsResponse],
    **BILLING_PAYMENTS_OPTIONS,
)
async def payment_filter_options(_user: PaymentsAccessReadDep) -> dict:
    return ok(
        data=PaymentFilterOptionsResponse(
            statuses=[s.value for s in PaymentRecordStatus if s != PaymentRecordStatus.VOIDED],
            allocation_statuses=[s.value for s in AllocationStatus],
            providers=[s.value for s in PaymentProvider],
        )
    )


@router.get(
    "/payments/invoice-candidates",
    response_model=SuccessResponse[PaginatedResponse[BillingPaymentInvoiceCandidateItem]],
    **BILLING_PAYMENTS_INVOICE_CANDIDATES,
)
async def list_payment_invoice_candidates(
    request: Request,
    response: Response,
    service: BillingServiceDep,
    user: PaymentsAccessReadDep,
    customer_id: str | None = Query(
        default=None,
        min_length=1,
        description=(
            "Payer user UUID (`users.id`): CUSTOMER_B2B contact user or CUSTOMER_B2C user — "
            "same as invoice customer_id; not the organisation id. Optional for ADMIN/SUPER_ADMIN "
            "when listing org-wide B2B candidates."
        ),
    ),
    client_type: str = Query(
        ...,
        min_length=1,
        description=(
            "Payer type: CUSTOMER_B2B / CUSTOMER_B2C (aliases B2B / B2C accepted). "
            "If CUSTOMER_B2C, customer_id is required."
        ),
    ),
    page: int = 1,
    size: int = 20,
    search: str | None = None,
    sort_by: str = "issue_date",
    sort_order: str = "desc",
    organization_id: str | None = Query(
        default=None,
        description=(
            "Organisation scope. **Required** for ADMIN and SUPER_ADMIN (must match the client's org). "
            "B2B callers are scoped from the token."
        ),
    ),
) -> dict:
    _ = response
    org_id = _org_scope(user, organization_id)
    ct = parse_record_payment_client_type(client_type)
    role_val = user.role.value if isinstance(user.role, UserRole) else str(user.role)
    is_admin_scope = role_val in (UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value)
    payer_id = str(customer_id or "").strip() or None
    if payer_id is None and not is_admin_scope:
        raise ValidationError(
            "customer_id is required for this billing operation",
            details=[
                {
                    "field": "customer_id",
                    "message": "Required for non-admin callers",
                    "type": "value_error",
                }
            ],
        )
    rows, total = await service.list_invoice_allocation_candidates(
        organization_id=org_id,
        customer_id=payer_id,
        client_type=ct,
        page=page,
        size=size,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items = [BillingPaymentInvoiceCandidateItem(**row) for row in rows]
    data = PaginatedResponse.create(items, total=total, page=page, size=size, request=request)
    return ok(data=data)


@router.get(
    "/payments/{payment_id}",
    response_model=SuccessResponse[BillingPaymentDetailResponse],
    **BILLING_PAYMENTS_GET,
)
async def get_payment_detail(
    payment_id: str,
    service: BillingServiceDep,
    user: PaymentsAccessReadDep,
    organization_id: str | None = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    payment, allocations = await service.get_payment_detail(organization_id=org_id, payment_id=payment_id)
    return ok(data=payment_to_detail(payment, allocations))


@router.patch(
    "/payments/{payment_id}/notes",
    response_model=SuccessResponse[BillingPaymentDetailResponse],
    **BILLING_PAYMENTS_NOTES_PATCH,
)
async def patch_payment_notes(
    payment_id: str,
    body: BillingPaymentNotesPatchRequest,
    service: BillingServiceDep,
    user: PaymentsAccessWriteDep,
    audit_ctx: AuditCtxDep,
    organization_id: str | None = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    await service.update_payment_notes(
        organization_id=org_id,
        payment_id=payment_id,
        notes=body.notes,
        actor_id=user.id,
        expected_version=body.version,
        audit_ctx=audit_ctx,
    )
    payment, allocations = await service.get_payment_detail(organization_id=org_id, payment_id=payment_id)
    return ok(data=payment_to_detail(payment, allocations))


@router.post(
    "/payments/{payment_id}/void",
    response_model=SuccessResponse[BillingPaymentDetailResponse],
    **BILLING_PAYMENTS_VOID,
)
async def void_payment(
    payment_id: str,
    body: BillingPaymentVoidRequest,
    service: BillingServiceDep,
    user: PaymentsAccessWriteDep,
    audit_ctx: AuditCtxDep,
    organization_id: str | None = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    await service.void_payment(
        organization_id=org_id,
        payment_id=payment_id,
        actor_id=user.id,
        reason=body.reason,
        expected_version=body.version,
        audit_ctx=audit_ctx,
    )
    payment, allocations = await service.get_payment_detail(organization_id=org_id, payment_id=payment_id)
    return ok(data=payment_to_detail(payment, allocations))


@router.post(
    "/payments",
    response_model=SuccessResponse[BillingPaymentDetailResponse],
    status_code=status.HTTP_201_CREATED,
    **BILLING_PAYMENTS_CREATE,
)
async def record_payment(
    body: BillingPaymentCreateRequest,
    service: BillingServiceDep,
    user: PaymentsAccessWriteDep,
    audit_ctx: AuditCtxDep,
    organization_id: str | None = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    payment = await service.record_payment(
        organization_id=org_id,
        customer_id=body.customer_id,
        client_type=parse_record_payment_client_type(body.client_type),
        amount=body.amount,
        payment_date=body.payment_date,
        recorded_by_id=user.id,
        status=PaymentRecordStatus(body.status),
        provider=PaymentProvider(body.provider),
        provider_txn_id=body.provider_txn_id,
        transaction_fee=body.transaction_fee,
        braintree_status=body.braintree_status,
        notes=body.notes,
        audit_ctx=audit_ctx,
    )
    payment, allocations = await service.get_payment_detail(organization_id=org_id, payment_id=payment.id)
    return ok(data=payment_to_detail(payment, allocations))


def _parse_payment_record_status(raw: str) -> PaymentRecordStatus:
    try:
        return PaymentRecordStatus(raw)
    except ValueError:
        allowed = ", ".join(sorted(s.value for s in PaymentRecordStatus))
        raise ValidationError(
            f"Invalid payment status {raw!r}. Allowed values: {allowed}",
            details=[
                {
                    "field": "status",
                    "message": f"Must be one of: {allowed}",
                    "type": "enum",
                }
            ],
        ) from None


def _parse_payment_provider(raw: str) -> PaymentProvider:
    try:
        return PaymentProvider(raw)
    except ValueError:
        allowed = ", ".join(sorted(p.value for p in PaymentProvider))
        raise ValidationError(
            f"Invalid payment provider {raw!r}. Allowed values: {allowed}",
            details=[
                {
                    "field": "provider",
                    "message": f"Must be one of: {allowed}",
                    "type": "enum",
                }
            ],
        ) from None


def _parse_multipart_allocations(
    *,
    allocations_json: str | None,
    allocation_invoice_id: str | None,
    allocation_allocated_amount: Decimal | None,
    allocation_notes: str | None,
) -> list[BillingPaymentAllocationUpsertRequest]:
    payload_json = str(allocations_json or "").strip()
    single_invoice_id = str(allocation_invoice_id or "").strip() or None
    single_amount = allocation_allocated_amount

    if payload_json:
        if single_invoice_id is not None or single_amount is not None or allocation_notes is not None:
            raise ValidationError(
                "Provide either allocations_json or single allocation form fields, not both",
                details=[
                    {
                        "field": "allocations_json",
                        "message": "Mutually exclusive with single allocation fields",
                        "type": "value_error",
                    }
                ],
            )
        try:
            decoded = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                "allocations_json must be valid JSON",
                details=[
                    {
                        "field": "allocations_json",
                        "message": str(exc),
                        "type": "json_invalid",
                    }
                ],
            ) from None

        try:
            if isinstance(decoded, list):
                parsed_bulk = BillingPaymentAllocationBulkRequest.model_validate({"allocations": decoded})
                return list(parsed_bulk.allocations)
            if isinstance(decoded, dict) and "allocations" in decoded:
                parsed_bulk = BillingPaymentAllocationBulkRequest.model_validate(decoded)
                return list(parsed_bulk.allocations)
            if isinstance(decoded, dict):
                parsed_single = BillingPaymentAllocationUpsertRequest.model_validate(decoded)
                return [parsed_single]
            raise ValidationError(
                "allocations_json must be an object or array",
                details=[
                    {
                        "field": "allocations_json",
                        "message": "Expected object for single allocation or object/array for bulk",
                        "type": "value_error",
                    }
                ],
            )
        except PydanticValidationError as exc:
            first = (exc.errors() or [{}])[0]
            msg = str(first.get("msg") or "Invalid allocations_json payload")
            raise ValidationError(
                "Invalid allocations_json payload",
                details=[
                    {
                        "field": "allocations_json",
                        "message": msg.replace("Value error, ", ""),
                        "type": str(first.get("type") or "value_error"),
                    }
                ],
            ) from None

    has_single = single_invoice_id is not None or single_amount is not None or allocation_notes is not None
    if not has_single:
        return []
    if single_invoice_id is None:
        raise ValidationError(
            "allocation_invoice_id is required when single allocation fields are provided",
            details=[
                {
                    "field": "allocation_invoice_id",
                    "message": "Required when adding multipart allocation",
                    "type": "value_error",
                }
            ],
        )
    if single_amount is None:
        raise ValidationError(
            "allocation_allocated_amount is required when single allocation fields are provided",
            details=[
                {
                    "field": "allocation_allocated_amount",
                    "message": "Required when adding multipart allocation",
                    "type": "value_error",
                }
            ],
        )
    return [
        BillingPaymentAllocationUpsertRequest(
            invoice_id=single_invoice_id,
            allocated_amount=single_amount,
            notes=allocation_notes,
        )
    ]


@router.post(
    "/payments/multipart",
    response_model=SuccessResponse[BillingPaymentDetailResponse],
    status_code=status.HTTP_201_CREATED,
    **BILLING_PAYMENTS_CREATE_MULTIPART,
)
async def record_payment_multipart(
    service: BillingServiceDep,
    user: PaymentsAccessWriteDep,
    audit_ctx: AuditCtxDep,
    amount: Decimal = Form(..., gt=0, description="Payment amount (GBP)"),
    payment_date: date = Form(..., description="Payment date (ISO 8601)"),
    customer_id: str | None = Form(default=None, min_length=1),
    client_type: str = Form(..., min_length=1),
    status_raw: str = Form(default="NOT_DEPOSITED", alias="status"),
    provider_raw: str = Form(default="MANUAL", alias="provider"),
    provider_txn_id: str | None = Form(default=None, max_length=255),
    transaction_fee: Decimal = Form(default=Decimal("0"), ge=0),
    braintree_status: str | None = Form(default=None, max_length=50),
    notes: str | None = Form(default=None, max_length=500),
    allocation_invoice_id: str | None = Form(
        default=None,
        min_length=1,
        description="Single-allocation mode: target invoice UUID. Mutually exclusive with allocations_json.",
    ),
    allocation_allocated_amount: Decimal | None = Form(
        default=None,
        gt=0,
        description="Single-allocation mode: amount to allocate (> 0). Required with allocation_invoice_id.",
    ),
    allocation_notes: str | None = Form(
        default=None,
        max_length=2000,
        description="Single-allocation mode: optional note (max 2000 chars).",
    ),
    allocations_json: str | None = Form(
        default=None,
        description=(
            "Alternative to single-allocation fields: JSON string — one object, "
            '{\"allocations\": [...]} wrapper, or array of 1–100 allocation objects.'
        ),
    ),
    remittance_advice: UploadFile | None = File(default=None, description="PNG, JPG/JPEG, or PDF — max 10 MB"),
    organization_id: str | None = Query(default=None),
) -> dict:
    """Record payment via multipart/form-data.

    See OpenAPI description on this operation for payment fields, remittance file rules,
    single vs bulk allocation (`allocation_*` form fields vs `allocations_json`), and validation.
    """
    org_id = _org_scope(user, organization_id)
    cust = str(customer_id or "").strip() or None
    st = _parse_payment_record_status(status_raw)
    prov = _parse_payment_provider(provider_raw)
    ct = parse_record_payment_client_type(client_type)
    allocation_rows = _parse_multipart_allocations(
        allocations_json=allocations_json,
        allocation_invoice_id=allocation_invoice_id,
        allocation_allocated_amount=allocation_allocated_amount,
        allocation_notes=allocation_notes,
    )

    remittance_tuple: tuple[bytes, str, str] | None = None
    if remittance_advice is not None and (remittance_advice.filename or "").strip():
        content, ctype = await validate_remittance_advice(remittance_advice)
        remittance_tuple = (content, ctype, remittance_advice.filename or "remittance")

    payment = await service.record_payment_with_allocations(
        organization_id=org_id,
        customer_id=cust,
        client_type=ct,
        amount=amount,
        payment_date=payment_date,
        recorded_by_id=user.id,
        status=st,
        provider=prov,
        provider_txn_id=provider_txn_id,
        transaction_fee=transaction_fee,
        braintree_status=braintree_status,
        notes=notes,
        audit_ctx=audit_ctx,
        remittance_advice=remittance_tuple,
        allocations=[
            {
                "invoice_id": row.invoice_id,
                "allocated_amount": row.allocated_amount,
                "notes": row.notes,
            }
            for row in allocation_rows
        ],
    )
    payment, allocations = await service.get_payment_detail(organization_id=org_id, payment_id=payment.id)
    return ok(data=payment_to_detail(payment, allocations))


@router.put(
    "/payments/{payment_id}/remittance-advice",
    response_model=SuccessResponse[BillingPaymentDetailResponse],
    **BILLING_PAYMENTS_REMITTANCE_PUT,
)
async def upload_remittance_advice(
    payment_id: str,
    service: BillingServiceDep,
    user: PaymentsAccessWriteDep,
    audit_ctx: AuditCtxDep,
    remittance_advice: UploadFile = File(..., description="PNG, JPG/JPEG, or PDF — max 10 MB"),
    organization_id: str | None = Query(default=None),
) -> dict:
    """Replace or set remittance advice; **422** if file is empty or fails magic-byte type/size checks."""
    org_id = _org_scope(user, organization_id)
    content, ctype = await validate_remittance_advice(remittance_advice)
    payment = await service.attach_remittance_advice(
        organization_id=org_id,
        payment_id=payment_id,
        content=content,
        content_type=ctype,
        original_filename=remittance_advice.filename or "remittance",
        actor_id=user.id,
        audit_ctx=audit_ctx,
    )
    payment, allocations = await service.get_payment_detail(organization_id=org_id, payment_id=payment.id)
    return ok(data=payment_to_detail(payment, allocations))


@router.delete(
    "/payments/{payment_id}/remittance-advice",
    response_model=SuccessResponse[BillingPaymentDetailResponse],
    **BILLING_PAYMENTS_REMITTANCE_DELETE,
)
async def delete_remittance_advice(
    payment_id: str,
    service: BillingServiceDep,
    user: PaymentsAccessWriteDep,
    audit_ctx: AuditCtxDep,
    organization_id: str | None = Query(default=None),
) -> dict:
    """Remove stored remittance advice; **404** if this payment has no attachment."""
    org_id = _org_scope(user, organization_id)
    payment = await service.delete_remittance_advice(
        organization_id=org_id,
        payment_id=payment_id,
        actor_id=user.id,
        audit_ctx=audit_ctx,
    )
    payment, allocations = await service.get_payment_detail(organization_id=org_id, payment_id=payment.id)
    return ok(data=payment_to_detail(payment, allocations))


@router.get(
    "/payments/{payment_id}/remittance-advice/signed-url",
    response_model=SuccessResponse[BillingRemittanceSignedUrlResponse],
    **BILLING_PAYMENTS_REMITTANCE_SIGNED_URL,
)
async def get_remittance_advice_signed_url(
    payment_id: str,
    service: BillingServiceDep,
    user: PaymentsAccessReadDep,
    disposition: Annotated[Literal["inline", "attachment"], Query(description="inline = view in browser; attachment = download")] = "inline",
    organization_id: str | None = Query(default=None),
) -> dict:
    """Presigned URL for viewing or downloading the attachment; **404** if none exists."""
    org_id = _org_scope(user, organization_id)
    payment, _allocations = await service.get_payment_detail(organization_id=org_id, payment_id=payment_id)
    url, expires_at = service.remittance_advice_signed_url(payment=payment, disposition=disposition)
    return ok(
        data=BillingRemittanceSignedUrlResponse(
            url=url,
            expires_at=expires_at.isoformat(),
            content_type=payment.remittance_advice_content_type or "application/octet-stream",
            disposition=disposition,
        )
    )


@router.post(
    "/payments/{payment_id}/allocations",
    response_model=SuccessResponse[BillingPaymentDetailResponse],
    **BILLING_PAYMENTS_ALLOCATE,
)
async def allocate_payment(
    payment_id: str,
    body: BillingPaymentAllocationUpsertRequest | BillingPaymentAllocationBulkRequest,
    service: BillingServiceDep,
    user: PaymentsAccessWriteDep,
    audit_ctx: AuditCtxDep,
    organization_id: str | None = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    allocations = body.allocations if isinstance(body, BillingPaymentAllocationBulkRequest) else [body]
    await service.add_or_revise_allocations(
        payment_id=payment_id,
        allocations=[
            {
                "invoice_id": row.invoice_id,
                "allocated_amount": row.allocated_amount,
                "notes": row.notes,
            }
            for row in allocations
        ],
        actor_id=user.id,
        audit_ctx=audit_ctx,
    )
    payment, allocations = await service.get_payment_detail(organization_id=org_id, payment_id=payment_id)
    return ok(data=payment_to_detail(payment, allocations))


@router.patch(
    "/payments/{payment_id}/allocations",
    response_model=SuccessResponse[BillingPaymentDetailResponse],
    **BILLING_PAYMENTS_ALLOCATIONS_REPLACE,
)
async def replace_payment_allocations(
    payment_id: str,
    body: BillingPaymentAllocationReplaceRequest,
    service: BillingServiceDep,
    user: PaymentsAccessWriteDep,
    audit_ctx: AuditCtxDep,
    organization_id: str | None = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    await service.replace_allocations(
        payment_id=payment_id,
        allocations=[
            {
                "invoice_id": row.invoice_id,
                "allocated_amount": row.allocated_amount,
                "notes": row.notes,
            }
            for row in body.allocations
        ],
        actor_id=user.id,
        audit_ctx=audit_ctx,
    )
    payment, allocations = await service.get_payment_detail(organization_id=org_id, payment_id=payment_id)
    return ok(data=payment_to_detail(payment, allocations))


@router.delete(
    "/payments/{payment_id}/allocations/{invoice_id}",
    response_model=SuccessResponse[BillingPaymentDetailResponse],
    **BILLING_PAYMENTS_ALLOCATIONS_REMOVE,
)
async def remove_payment_allocation(
    payment_id: str,
    invoice_id: str,
    service: BillingServiceDep,
    user: PaymentsAccessWriteDep,
    audit_ctx: AuditCtxDep,
    organization_id: str | None = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    await service.remove_allocation(
        payment_id=payment_id,
        invoice_id=invoice_id,
        actor_id=user.id,
        audit_ctx=audit_ctx,
    )
    payment, allocations = await service.get_payment_detail(organization_id=org_id, payment_id=payment_id)
    return ok(data=payment_to_detail(payment, allocations))


@router.get(
    "/refunds",
    response_model=SuccessResponse[PaginatedResponse[RefundListItem]],
    **BILLING_REFUNDS_LIST,
)
async def list_refunds(
    request: Request,
    service: BillingServiceDep,
    user: RefundAdminReadDep,
    page: int = 1,
    size: int = 20,
    organization_id: str | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status_filter: Annotated[list[RefundStatus] | None, Query(alias="status")] = None,
    refund_type: Annotated[list[RefundType] | None, Query()] = None,
    refund_method: Annotated[list[RefundMethod] | None, Query()] = None,
    reason_category: Annotated[list[RefundReasonCategory] | None, Query()] = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    items, total = await service.list_refunds(
        organization_id=org_id,
        page=page,
        size=size,
        search=search,
        status=[s.value for s in (status_filter or [])] or None,
        refund_type=[s.value for s in (refund_type or [])] or None,
        refund_method=[s.value for s in (refund_method or [])] or None,
        reason_category=[s.value for s in (reason_category or [])] or None,
        date_from=date_from,
        date_to=date_to,
    )
    data = PaginatedResponse.create([refund_to_list_item(r) for r in items], total=total, page=page, size=size, request=request)
    return ok(data=data)


@router.get(
    "/refunds/kpis",
    response_model=SuccessResponse[RefundKpisResponse],
    **BILLING_REFUNDS_KPIS,
)
async def refund_kpis(
    service: BillingServiceDep,
    user: RefundAdminReadDep,
    organization_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status_filter: Annotated[list[RefundStatus] | None, Query(alias="status")] = None,
    refund_type: Annotated[list[RefundType] | None, Query()] = None,
    refund_method: Annotated[list[RefundMethod] | None, Query()] = None,
    reason_category: Annotated[list[RefundReasonCategory] | None, Query()] = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    kpi = await service.refund_kpis(
        organization_id=org_id,
        date_from=date_from,
        date_to=date_to,
        status=[s.value for s in (status_filter or [])] or None,
        refund_type=[s.value for s in (refund_type or [])] or None,
        refund_method=[s.value for s in (refund_method or [])] or None,
        reason_category=[s.value for s in (reason_category or [])] or None,
    )
    return ok(data=RefundKpisResponse(**kpi))


@router.get(
    "/refunds/options",
    response_model=SuccessResponse[RefundFilterOptionsResponse],
    **BILLING_REFUNDS_OPTIONS,
)
async def refund_options(_user: RefundAdminReadDep) -> dict:
    return ok(
        data=RefundFilterOptionsResponse(
            statuses=["INITIATED", "PROCESSING", "COMPLETED", "FAILED", "REVERSED"],
            refund_types=["FULL", "PARTIAL"],
            refund_methods=["CARD_REFUND", "BANK_TRANSFER", "CREDIT_NOTE"],
            reason_categories=[
                "BOOKING_CANCELLED",
                "SERVICE_FAILURE",
                "DUPLICATE_PAYMENT",
                "BILLING_ERROR",
                "CLIENT_REQUEST",
                "VOIDED_INVOICE",
                "OTHER",
            ],
        )
    )


@router.get(
    "/refunds/{refund_id}",
    response_model=SuccessResponse[RefundDetailWithEventsResponse],
    **BILLING_REFUNDS_GET,
)
async def get_refund(
    refund_id: str,
    service: BillingServiceDep,
    user: RefundAdminReadDep,
    organization_id: str | None = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    refund, events = await service.get_refund_detail(organization_id=org_id, refund_id=refund_id)
    return ok(
        data=RefundDetailWithEventsResponse(
            refund=refund_to_detail(refund),
            events=[{"id": e.id, "event_type": e.event_type, "actor_id": e.actor_id, "payload_json": e.payload_json, "created_at": e.created_at} for e in events],
        )
    )


@router.post(
    "/refunds",
    response_model=SuccessResponse[RefundDetailResponse],
    status_code=status.HTTP_201_CREATED,
    **BILLING_REFUNDS_CREATE,
)
async def create_refund(
    body: RefundCreateRequest,
    service: BillingServiceDep,
    user: RefundAdminWriteDep,
    organization_id: str | None = None,
    x_idempotency_key: Annotated[str | None, Query(alias="idempotency_key")] = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    refund = await service.create_refund(
        organization_id=org_id,
        billing_payment_id=body.billing_payment_id,
        amount=body.amount,
        refund_type=RefundType(body.refund_type),
        refund_method=RefundMethod(body.refund_method),
        reason_category=RefundReasonCategory(body.reason_category),
        reason_description=body.reason_description,
        actor_id=user.id,
        invoice_id=body.invoice_id,
        linked_booking_ref=body.linked_booking_ref,
        metadata_json=body.metadata_json,
        idempotency_key=x_idempotency_key,
    )
    return ok(data=refund_to_detail(refund))


@router.post(
    "/refunds/{refund_id}/mark-complete",
    response_model=SuccessResponse[RefundDetailResponse],
    **BILLING_REFUNDS_MARK_COMPLETE,
)
async def mark_refund_complete(
    refund_id: str,
    body: RefundActionRequest,
    service: BillingServiceDep,
    user: RefundAdminWriteDep,
    organization_id: str | None = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    refund = await service.mark_refund_complete(
        organization_id=org_id,
        refund_id=refund_id,
        actor_id=user.id,
        braintree_status=body.braintree_status,
        note=body.note,
    )
    return ok(data=refund_to_detail(refund))


@router.post(
    "/refunds/{refund_id}/retry",
    response_model=SuccessResponse[RefundDetailResponse],
    **BILLING_REFUNDS_RETRY,
)
async def retry_refund(
    refund_id: str,
    body: RefundActionRequest,
    service: BillingServiceDep,
    user: RefundAdminWriteDep,
    organization_id: str | None = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    refund = await service.retry_refund(
        organization_id=org_id,
        refund_id=refund_id,
        actor_id=user.id,
        failure_code=body.failure_code,
        failure_message=body.failure_message,
    )
    return ok(data=refund_to_detail(refund))


@router.post(
    "/refunds/{refund_id}/issue-credit-note",
    response_model=SuccessResponse[RefundDetailResponse],
    **BILLING_REFUNDS_ISSUE_CREDIT_NOTE,
)
async def issue_refund_credit_note(
    refund_id: str,
    body: RefundActionRequest,
    service: BillingServiceDep,
    user: RefundAdminWriteDep,
    organization_id: str | None = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    refund = await service.issue_credit_note_for_refund(
        organization_id=org_id,
        refund_id=refund_id,
        actor_id=user.id,
        note=body.note,
    )
    return ok(data=refund_to_detail(refund))


@router.get(
    "/b2b/refunds",
    response_model=SuccessResponse[PaginatedResponse[RefundListItem]],
    **BILLING_REFUNDS_LIST,
)
async def b2b_list_refunds(
    request: Request,
    service: BillingServiceDep,
    user: RefundB2BReadDep,
    page: int = 1,
    size: int = 20,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status_filter: Annotated[list[RefundStatus] | None, Query(alias="status")] = None,
    refund_type: Annotated[list[RefundType] | None, Query()] = None,
    refund_method: Annotated[list[RefundMethod] | None, Query()] = None,
    reason_category: Annotated[list[RefundReasonCategory] | None, Query()] = None,
) -> dict:
    org_id = _org_scope(user, None)
    items, total = await service.list_refunds(
        organization_id=org_id,
        page=page,
        size=size,
        search=search,
        status=[s.value for s in (status_filter or [])] or None,
        refund_type=[s.value for s in (refund_type or [])] or None,
        refund_method=[s.value for s in (refund_method or [])] or None,
        reason_category=[s.value for s in (reason_category or [])] or None,
        date_from=date_from,
        date_to=date_to,
    )
    data = PaginatedResponse.create([refund_to_list_item(r) for r in items], total=total, page=page, size=size, request=request)
    return ok(data=data)


@router.get(
    "/b2b/refunds/kpis",
    response_model=SuccessResponse[RefundKpisResponse],
    **BILLING_REFUNDS_KPIS,
)
async def b2b_refund_kpis(
    service: BillingServiceDep,
    user: RefundB2BReadDep,
    date_from: date | None = None,
    date_to: date | None = None,
    status_filter: Annotated[list[RefundStatus] | None, Query(alias="status")] = None,
    refund_type: Annotated[list[RefundType] | None, Query()] = None,
    refund_method: Annotated[list[RefundMethod] | None, Query()] = None,
    reason_category: Annotated[list[RefundReasonCategory] | None, Query()] = None,
) -> dict:
    org_id = _org_scope(user, None)
    kpi = await service.refund_kpis(
        organization_id=org_id,
        date_from=date_from,
        date_to=date_to,
        status=[s.value for s in (status_filter or [])] or None,
        refund_type=[s.value for s in (refund_type or [])] or None,
        refund_method=[s.value for s in (refund_method or [])] or None,
        reason_category=[s.value for s in (reason_category or [])] or None,
    )
    return ok(data=RefundKpisResponse(**kpi))


@router.get(
    "/b2b/refunds/options",
    response_model=SuccessResponse[RefundFilterOptionsResponse],
    **BILLING_REFUNDS_OPTIONS,
)
async def b2b_refund_options(_user: RefundB2BReadDep) -> dict:
    return ok(
        data=RefundFilterOptionsResponse(
            statuses=["INITIATED", "PROCESSING", "COMPLETED", "FAILED", "REVERSED"],
            refund_types=["FULL", "PARTIAL"],
            refund_methods=["CARD_REFUND", "BANK_TRANSFER", "CREDIT_NOTE"],
            reason_categories=[
                "BOOKING_CANCELLED",
                "SERVICE_FAILURE",
                "DUPLICATE_PAYMENT",
                "BILLING_ERROR",
                "CLIENT_REQUEST",
                "VOIDED_INVOICE",
                "OTHER",
            ],
        )
    )


@router.get(
    "/b2b/refunds/{refund_id}",
    response_model=SuccessResponse[RefundDetailWithEventsResponse],
    **BILLING_REFUNDS_GET,
)
async def b2b_get_refund(
    refund_id: str,
    service: BillingServiceDep,
    user: RefundB2BReadDep,
) -> dict:
    org_id = _org_scope(user, None)
    refund, events = await service.get_refund_detail(organization_id=org_id, refund_id=refund_id)
    return ok(
        data=RefundDetailWithEventsResponse(
            refund=refund_to_detail(refund),
            events=[{"id": e.id, "event_type": e.event_type, "actor_id": e.actor_id, "payload_json": e.payload_json, "created_at": e.created_at} for e in events],
        )
    )


@router.get(
    "/b2b/credit-notes",
    response_model=SuccessResponse[PaginatedResponse[CreditNoteListItem]],
    **BILLING_B2B_CREDIT_NOTES_LIST,
)
async def b2b_list_credit_notes(
    request: Request,
    service: BillingServiceDep,
    user: RefundB2BReadDep,
    page: int = 1,
    size: int = 20,
    customer_id: Annotated[
        str | None,
        Query(
            description=(
                "Optional filter by B2B customer user UUID. Omit for all org credit notes; "
                "pass empty string for unassigned notes only."
            ),
        ),
    ] = None,
    search: str | None = None,
    status_filter: Annotated[list[str] | None, Query(alias="status")] = None,
    reason_category: Annotated[list[str] | None, Query()] = None,
    issued_from: date | None = None,
    issued_to: date | None = None,
    sort_by: str = "issue_date",
    sort_order: str = "desc",
) -> dict:
    _ = user
    org_id = _org_scope(user, None)
    customer_filter = parse_b2b_credit_note_customer_filter(customer_id)
    items, total = await service.list_credit_notes_for_b2b(
        organization_id=org_id,
        customer_filter=customer_filter,
        page=page,
        size=size,
        search=search,
        status=status_filter,
        reason_category=reason_category,
        issued_from=issued_from,
        issued_to=issued_to,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    data = PaginatedResponse.create([credit_note_to_list_item(cn) for cn in items], total=total, page=page, size=size, request=request)
    return ok(data=data)


@router.get(
    "/b2b/credit-notes/{credit_note_id}",
    response_model=SuccessResponse[CreditNoteDetailResponse],
    **BILLING_B2B_CREDIT_NOTES_GET,
)
async def b2b_get_credit_note(
    credit_note_id: str,
    service: BillingServiceDep,
    user: RefundB2BReadDep,
) -> dict:
    org_id = _org_scope(user, None)
    cn = await service.get_credit_note_detail(
        credit_note_id=credit_note_id,
        organization_id=org_id,
        b2b_org_scope=True,
    )
    applications = await service._credit_app_repo.list_for_credit_note(cn.id)
    applied_total = await service._credit_app_repo.get_applied_total_for_credit_note(cn.id)
    return ok(data=credit_note_to_detail(cn, applied_total, applications))


@router.get(
    "/b2b/credit-notes/{credit_note_id}/invoice-candidates",
    response_model=SuccessResponse[PaginatedResponse[CreditNoteInvoiceCandidateItem]],
    **BILLING_B2B_CREDIT_NOTES_CANDIDATES,
)
async def b2b_credit_note_candidates(
    request: Request,
    credit_note_id: str,
    service: BillingServiceDep,
    user: RefundB2BReadDep,
    page: int = 1,
    size: int = 20,
    search: str | None = None,
) -> dict:
    org_id = _org_scope(user, None)
    rows, total = await service.list_credit_note_invoice_candidates(
        credit_note_id=credit_note_id,
        organization_id=org_id,
        b2b_org_scope=True,
        page=page,
        size=size,
        search=search,
    )
    items = [CreditNoteInvoiceCandidateItem(**row) for row in rows]
    data = PaginatedResponse.create(items, total=total, page=page, size=size, request=request)
    return ok(data=data)


@router.post(
    "/b2b/credit-notes/{credit_note_id}/apply",
    response_model=SuccessResponse[CreditNoteApplyResponse],
    **BILLING_B2B_CREDIT_NOTES_APPLY,
)
async def b2b_apply_credit_note(
    credit_note_id: str,
    body: CreditNoteApplyRequest,
    service: BillingServiceDep,
    user: RefundB2BWriteDep,
) -> dict:
    org_id = _org_scope(user, None)
    app = await service.apply_credit_note_auto(
        credit_note_id=credit_note_id,
        invoice_id=body.invoice_id,
        organization_id=org_id,
        actor_id=user.id,
        b2b_org_scope=True,
    )
    return ok(
        data=CreditNoteApplyResponse(
            credit_note_id=app.credit_note_id,
            invoice_id=app.invoice_id,
            applied_amount=app.applied_amount,
            applied_at=app.applied_at,
        )
    )


@router.post(
    "/b2b/credit-notes/{credit_note_id}/pdf",
    response_model=SuccessResponse[CreditNotePdfStatusResponse],
    **BILLING_B2B_CREDIT_NOTES_PDF_REQUEST,
)
async def b2b_request_credit_note_pdf(
    credit_note_id: str,
    service: BillingServiceDep,
    user: RefundB2BReadDep,
) -> dict:
    org_id = _org_scope(user, None)
    data, _artifact = await service.request_credit_note_pdf(
        credit_note_id=credit_note_id,
        organization_id=org_id,
        b2b_org_scope=True,
    )
    return ok(data=CreditNotePdfStatusResponse(**data))


@router.get(
    "/b2b/credit-notes/{credit_note_id}/pdf",
    response_model=SuccessResponse[CreditNotePdfStatusResponse],
    **BILLING_B2B_CREDIT_NOTES_PDF_STATUS,
)
async def b2b_credit_note_pdf_status(
    credit_note_id: str,
    service: BillingServiceDep,
    user: RefundB2BReadDep,
) -> dict:
    org_id = _org_scope(user, None)
    data = await service.get_credit_note_pdf_status(
        credit_note_id=credit_note_id,
        organization_id=org_id,
        b2b_org_scope=True,
    )
    return ok(data=CreditNotePdfStatusResponse(**data))


@router.post(
    "/b2b/credit-notes/{credit_note_id}/pdf/signed-url",
    response_model=SuccessResponse[CreditNotePdfSignedUrlResponse],
    **BILLING_B2B_CREDIT_NOTES_PDF_SIGNED_URL,
)
async def b2b_credit_note_pdf_signed_url(
    credit_note_id: str,
    body: CreditNotePdfSignedUrlRequest,
    service: BillingServiceDep,
    user: RefundB2BReadDep,
) -> dict:
    org_id = _org_scope(user, None)
    url, expires_at = await service.get_credit_note_pdf_signed_url(
        credit_note_id=credit_note_id,
        organization_id=org_id,
        b2b_org_scope=True,
        disposition=body.disposition,
    )
    return ok(data=CreditNotePdfSignedUrlResponse(url=url, expires_at=expires_at.isoformat(), disposition=body.disposition))


@router.post(
    "/credit-notes",
    response_model=SuccessResponse[CreditNoteDetailResponse],
    status_code=status.HTTP_201_CREATED,
    **BILLING_ADMIN_CREDIT_NOTES_CREATE,
)
async def admin_create_credit_note(
    body: CreditNoteCreateRequest,
    service: BillingServiceDep,
    user: CreditNotesAdminWriteDep,
) -> dict:
    _ = user
    cn = await service.create_credit_note(
        organization_id=body.organization_id,
        source_invoice_id=body.source_invoice_id,
        customer_id=body.customer_id,
        issue_date_value=body.issue_date,
        amount=body.amount,
        reason_category=body.reason_category,
        reason=body.reason,
    )
    applications = await service._credit_app_repo.list_for_credit_note(cn.id)
    applied_total = await service._credit_app_repo.get_applied_total_for_credit_note(cn.id)
    return ok(data=credit_note_to_detail(cn, applied_total, applications))


@router.get(
    "/credit-notes",
    response_model=SuccessResponse[PaginatedResponse[CreditNoteListItem]],
    **BILLING_ADMIN_CREDIT_NOTES_LIST,
)
async def admin_list_credit_notes(
    request: Request,
    service: BillingServiceDep,
    user: CreditNotesAdminReadDep,
    organization_id: Annotated[
        str,
        Query(description="Organisation UUID (required for ADMIN / SUPER_ADMIN)."),
    ],
    page: int = 1,
    size: int = 20,
    search: str | None = None,
    status_filter: Annotated[list[str] | None, Query(alias="status")] = None,
    reason_category: Annotated[list[str] | None, Query()] = None,
    issued_from: date | None = None,
    issued_to: date | None = None,
    sort_by: str = "issue_date",
    sort_order: str = "desc",
) -> dict:
    org_id = _org_scope(user, organization_id)
    items, total = await service.list_credit_notes(
        organization_id=org_id,
        page=page,
        size=size,
        search=search,
        status=status_filter,
        reason_category=reason_category,
        issued_from=issued_from,
        issued_to=issued_to,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    data = PaginatedResponse.create([credit_note_to_list_item(cn) for cn in items], total=total, page=page, size=size, request=request)
    return ok(data=data)


@router.get(
    "/credit-notes/{credit_note_id}",
    response_model=SuccessResponse[CreditNoteDetailResponse],
    **BILLING_ADMIN_CREDIT_NOTES_GET,
)
async def admin_get_credit_note(
    credit_note_id: str,
    organization_id: Annotated[
        str,
        Query(description="Organisation UUID (required for ADMIN / SUPER_ADMIN)."),
    ],
    service: BillingServiceDep,
    user: CreditNotesAdminReadDep,
) -> dict:
    org_id = _org_scope(user, organization_id)
    cn = await service.get_credit_note_detail(credit_note_id=credit_note_id, organization_id=org_id)
    applications = await service._credit_app_repo.list_for_credit_note(cn.id)
    applied_total = await service._credit_app_repo.get_applied_total_for_credit_note(cn.id)
    return ok(data=credit_note_to_detail(cn, applied_total, applications))


@router.get(
    "/credit-notes/{credit_note_id}/invoice-candidates",
    response_model=SuccessResponse[PaginatedResponse[CreditNoteInvoiceCandidateItem]],
    **BILLING_ADMIN_CREDIT_NOTES_CANDIDATES,
)
async def admin_credit_note_invoice_candidates(
    request: Request,
    credit_note_id: str,
    organization_id: Annotated[
        str,
        Query(description="Organisation UUID (required for ADMIN / SUPER_ADMIN)."),
    ],
    service: BillingServiceDep,
    user: CreditNotesAdminReadDep,
    page: int = 1,
    size: int = 20,
    search: str | None = None,
) -> dict:
    org_id = _org_scope(user, organization_id)
    rows, total = await service.list_credit_note_invoice_candidates(
        credit_note_id=credit_note_id,
        organization_id=org_id,
        page=page,
        size=size,
        search=search,
    )
    items = [CreditNoteInvoiceCandidateItem(**row) for row in rows]
    data = PaginatedResponse.create(items, total=total, page=page, size=size, request=request)
    return ok(data=data)


@router.post(
    "/credit-notes/{credit_note_id}/apply",
    response_model=SuccessResponse[CreditNoteApplyResponse],
    **BILLING_ADMIN_CREDIT_NOTES_APPLY,
)
async def admin_apply_credit_note(
    credit_note_id: str,
    organization_id: Annotated[
        str,
        Query(description="Organisation UUID (required for ADMIN / SUPER_ADMIN)."),
    ],
    body: CreditNoteApplyRequest,
    service: BillingServiceDep,
    user: CreditNotesAdminWriteDep,
) -> dict:
    org_id = _org_scope(user, organization_id)
    app = await service.apply_credit_note_auto(
        credit_note_id=credit_note_id,
        invoice_id=body.invoice_id,
        organization_id=org_id,
        actor_id=user.id,
    )
    return ok(
        data=CreditNoteApplyResponse(
            credit_note_id=app.credit_note_id,
            invoice_id=app.invoice_id,
            applied_amount=app.applied_amount,
            applied_at=app.applied_at,
        )
    )


@router.post(
    "/credit-notes/{credit_note_id}/void",
    response_model=SuccessResponse[CreditNoteDetailResponse],
    **BILLING_ADMIN_CREDIT_NOTES_VOID,
)
async def admin_void_credit_note(
    credit_note_id: str,
    organization_id: Annotated[
        str,
        Query(description="Organisation UUID (required for ADMIN / SUPER_ADMIN)."),
    ],
    body: CreditNoteVoidRequest,
    service: BillingServiceDep,
    user: CreditNotesAdminWriteDep,
) -> dict:
    org_id = _org_scope(user, organization_id)
    cn = await service.void_credit_note(
        credit_note_id=credit_note_id,
        organization_id=org_id,
        reason=body.reason,
        actor_id=user.id,
        actor_role=user.role,
    )
    applications = await service._credit_app_repo.list_for_credit_note(cn.id)
    applied_total = await service._credit_app_repo.get_applied_total_for_credit_note(cn.id)
    return ok(data=credit_note_to_detail(cn, applied_total, applications))


@router.post(
    "/credit-notes/{credit_note_id}/pdf",
    response_model=SuccessResponse[CreditNotePdfStatusResponse],
    **BILLING_B2B_CREDIT_NOTES_PDF_REQUEST,
)
async def admin_request_credit_note_pdf(
    credit_note_id: str,
    organization_id: Annotated[str, Query(description="Organisation UUID (required for ADMIN / SUPER_ADMIN).")],
    service: BillingServiceDep,
    user: CreditNotesAdminReadDep,
) -> dict:
    org_id = _org_scope(user, organization_id)
    data, _artifact = await service.request_credit_note_pdf(
        credit_note_id=credit_note_id,
        organization_id=org_id,
    )
    return ok(data=CreditNotePdfStatusResponse(**data))


@router.get(
    "/credit-notes/{credit_note_id}/pdf",
    response_model=SuccessResponse[CreditNotePdfStatusResponse],
    **BILLING_B2B_CREDIT_NOTES_PDF_STATUS,
)
async def admin_credit_note_pdf_status(
    credit_note_id: str,
    organization_id: Annotated[str, Query(description="Organisation UUID (required for ADMIN / SUPER_ADMIN).")],
    service: BillingServiceDep,
    user: CreditNotesAdminReadDep,
) -> dict:
    org_id = _org_scope(user, organization_id)
    data = await service.get_credit_note_pdf_status(
        credit_note_id=credit_note_id,
        organization_id=org_id,
    )
    return ok(data=CreditNotePdfStatusResponse(**data))


@router.post(
    "/credit-notes/{credit_note_id}/pdf/signed-url",
    response_model=SuccessResponse[CreditNotePdfSignedUrlResponse],
    **BILLING_B2B_CREDIT_NOTES_PDF_SIGNED_URL,
)
async def admin_credit_note_pdf_signed_url(
    credit_note_id: str,
    organization_id: Annotated[str, Query(description="Organisation UUID (required for ADMIN / SUPER_ADMIN).")],
    body: CreditNotePdfSignedUrlRequest,
    service: BillingServiceDep,
    user: CreditNotesAdminReadDep,
) -> dict:
    org_id = _org_scope(user, organization_id)
    url, expires_at = await service.get_credit_note_pdf_signed_url(
        credit_note_id=credit_note_id,
        organization_id=org_id,
        disposition=body.disposition,
    )
    return ok(data=CreditNotePdfSignedUrlResponse(url=url, expires_at=expires_at.isoformat(), disposition=body.disposition))


@router.get(
    "/credit-notes/{credit_note_id}/client-email",
    response_model=SuccessResponse[CreditNoteClientEmailResponse],
    **BILLING_ADMIN_CREDIT_NOTES_CLIENT_EMAIL,
)
async def admin_credit_note_client_email(
    credit_note_id: str,
    organization_id: Annotated[
        str,
        Query(description="Organisation UUID (required for ADMIN / SUPER_ADMIN)."),
    ],
    service: BillingServiceDep,
    user: CreditNotesAdminReadDep,
) -> dict:
    org_id = _org_scope(user, organization_id)
    email = await service.get_credit_note_client_email(credit_note_id=credit_note_id, organization_id=org_id)
    return ok(data=CreditNoteClientEmailResponse(email=email))


@router.post(
    "/credit-notes/{credit_note_id}/send-to-client",
    response_model=SuccessResponse[CreditNoteDetailResponse],
    **BILLING_ADMIN_CREDIT_NOTES_SEND,
)
async def admin_send_credit_note(
    credit_note_id: str,
    organization_id: Annotated[
        str,
        Query(description="Organisation UUID (required for ADMIN / SUPER_ADMIN)."),
    ],
    body: CreditNoteSendRequest,
    service: BillingServiceDep,
    user: CreditNotesAdminWriteDep,
) -> dict:
    org_id = _org_scope(user, organization_id)
    recipient = body.email or await service.get_credit_note_client_email(credit_note_id=credit_note_id, organization_id=org_id)
    if not recipient:
        raise ValidationError("No client email available for this credit note")
    cn = await service.send_credit_note_to_client(
        credit_note_id=credit_note_id,
        organization_id=org_id,
        recipient_email=recipient,
        actor_id=user.id,
    )
    applications = await service._credit_app_repo.list_for_credit_note(cn.id)
    applied_total = await service._credit_app_repo.get_applied_total_for_credit_note(cn.id)
    return ok(data=credit_note_to_detail(cn, applied_total, applications))
