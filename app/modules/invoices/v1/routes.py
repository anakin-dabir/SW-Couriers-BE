"""Invoices v1 API: list, create/update/delete draft, finalize, void, write-off, PDF request and signed URL.

Routes are scoped by organization for B2B (user.organization_id); admin has no org filter.
Create supports finalize=true for single-step Create & Finalise. Void/write-off require reason.
PDF: POST .../pdf requests generation (dedupe by signature); GET .../pdf polls status; POST .../pdf/signed-url returns short-lived download link.

Deferred (product backlog): account **statements** — multi-invoice period export, e.g. POST /v1/invoices/statements
with date range, async job + poll + signed URL (mirror single-invoice PDF pattern), org-scoped for B2B and
admin-selectable org; cap date span / rate-limit. Not implemented yet.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status

from app.common.deps import Allowed, AuthUser
from app.common.enums import ClientType, UserRole
from app.common.enums.permission import PermissionLevel, Resource
from app.common.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.common.response import ok
from app.common.schemas import PaginatedResponse, SuccessResponse, quantize_currency
from app.core.rate_limit import DRIVERS_READ_RATE_LIMIT, DRIVERS_WRITE_RATE_LIMIT, limiter
from app.modules.invoices.enums import InvoiceStatus, PaymentStatus
from app.modules.invoices.models import Invoice
from app.modules.invoices.service import InvoiceService
from app.modules.invoices.v1.docs import (
    INVOICES_CREATE,
    INVOICES_DELETE_DRAFT,
    INVOICES_FINALIZE,
    INVOICES_INTERNAL_NOTE_CREATE,
    INVOICES_INTERNAL_NOTE_DELETE,
    INVOICES_INTERNAL_NOTE_GET,
    INVOICES_INTERNAL_NOTE_UPDATE,
    INVOICES_GET,
    INVOICES_INVOICE_PAYMENTS,
    INVOICES_LIST,
    INVOICES_PDF_REQUEST,
    INVOICES_PDF_SIGNED_URL,
    INVOICES_PDF_STATUS,
    INVOICES_SUMMARY,
    INVOICES_UPDATE,
    INVOICES_VOID,
    INVOICES_WRITE_OFF,
)
from app.modules.invoices.v1.schemas import (
    AppliedCreditNoteEntry,
    InvoiceCreateRequest,
    InvoiceDetailResponse,
    InvoiceEventEntry,
    InvoiceLineItemEntry,
    InvoiceListItem,
    InvoiceInternalNoteResponse,
    InvoiceInternalNoteWriteRequest,
    InvoicePaymentHistoryItem,
    InvoiceRefundSummary,
    InvoiceSummaryResponse,
    InvoiceUpdateRequest,
    InvoiceVoidRequest,
    InvoiceWriteOffRequest,
    PdfStatusResponse,
    SignedUrlRequest,
    SignedUrlResponse,
)

router = APIRouter()

InvoiceServiceDep = Annotated[InvoiceService, Depends(InvoiceService.dep)]

_BILLING_ADMIN_ROLES = frozenset({UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value})

_ALLOWED_USERS = (UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.CUSTOMER_B2B, UserRole.CUSTOMER_B2C)
InvoiceReadDep = Annotated[AuthUser, Allowed(*_ALLOWED_USERS, resource=Resource.BILLING, level=PermissionLevel.READ)]
InvoiceWriteDep = Annotated[AuthUser, Allowed(*_ALLOWED_USERS, resource=Resource.BILLING, level=PermissionLevel.WRITE)]


def _read_scope(user: AuthUser) -> tuple[str | None, str | None]:
    """Read scope: B2C by customer_id, B2B by organization_id, super-admin unrestricted."""
    if user.role == UserRole.SUPER_ADMIN.value:
        return None, None
    if user.client_type == ClientType.CUSTOMER_B2C:
        return None, user.id
    if user.client_type == ClientType.CUSTOMER_B2B:
        if not user.organization_id:
            raise ForbiddenError("Tenant context missing for invoice access")
        return user.organization_id, None
    return None, None


def _effective_list_scope(user: AuthUser, organization_id: str | None) -> tuple[str | None, str | None]:
    """Scope for list/summary: optional organization_id narrows admin/super-admin; B2B cannot escape JWT org."""
    role_val = user.role if isinstance(user.role, str) else user.role.value

    if organization_id is not None:
        if user.client_type == ClientType.CUSTOMER_B2B:
            if not user.organization_id or str(user.organization_id) != str(organization_id):
                raise ForbiddenError("Cannot access invoices for another organisation.")
            return user.organization_id, None
        if user.client_type == ClientType.CUSTOMER_B2C:
            raise ForbiddenError("organization_id is not applicable for this client type.")
        if role_val not in (UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value):
            raise ForbiddenError("organization_id filter is only available for administrators.")
        return organization_id, None

    return _read_scope(user)


def _org_scope(user: AuthUser) -> str | None:
    """Organization ID for write scope where needed."""
    return user.organization_id if user.client_type == ClientType.CUSTOMER_B2B else None


def _require_billing_admin(user: AuthUser) -> None:
    role_val = user.role if isinstance(user.role, str) else user.role.value
    if role_val not in _BILLING_ADMIN_ROLES:
        raise ForbiddenError("Only administrators can manage invoice internal notes")


def _internal_note_response(invoice: Invoice) -> InvoiceInternalNoteResponse:
    notes = InvoiceService.display_internal_note(invoice.notes)
    return InvoiceInternalNoteResponse(
        invoice_id=invoice.id,
        notes=notes,
        has_note=notes is not None,
        invoice_status=invoice.status,
        updated_at=invoice.updated_at,
        version=invoice.version,
    )


def _invoice_event_display_title(event_type: str, reason: str | None) -> str:
    labels = {
        "CREATED": "Invoice created",
        "DRAFT_SAVED": "Draft saved",
        "FINALIZED": "Invoice finalised",
        "VOIDED": "Invoice voided",
        "WRITTEN_OFF": "Invoice written off",
        "CREDIT_APPLIED": "Credit note applied",
    }
    base = labels.get(event_type, event_type.replace("_", " ").title())
    if reason and event_type in ("VOIDED", "WRITTEN_OFF"):
        r = reason.strip()
        if len(r) > 120:
            r = f"{r[:117]}..."
        return f"{base}: {r}"
    return base


def _refund_summary_from_row(raw: dict[str, Any] | None) -> InvoiceRefundSummary:
    r = raw or {}
    amt = r.get("refunded_amount", 0)
    if not isinstance(amt, Decimal):
        amt = Decimal(str(amt))
    pr = r.get("pending_refund_count", 0)
    cr = r.get("completed_refund_count", 0)
    return InvoiceRefundSummary(
        refunded_amount=quantize_currency(amt),
        pending_refund_count=int(pr) if isinstance(pr, int) else int(str(pr)),
        completed_refund_count=int(cr) if isinstance(cr, int) else int(str(cr)),
    )


async def _invoice_detail_response(
    service: InvoiceService,
    invoice,
    *,
    credit_total: Decimal,
    payment_method: str | None = None,
) -> InvoiceDetailResponse:
    rmap = await service.refund_summaries_for_invoice_ids([invoice.id])
    dset = await service.invoice_ids_with_open_dispute([invoice.id])
    return _invoice_to_detail_response(
        invoice,
        credit_total=credit_total,
        payment_method=payment_method,
        refund_summary=_refund_summary_from_row(rmap.get(invoice.id)),
        has_open_dispute=invoice.id in dset,
    )


def _invoice_to_list_item(
    invoice,
    refund_row: dict[str, Any] | None = None,
    has_open_dispute: bool = False,
    *,
    credit_total: Decimal = Decimal("0"),
) -> InvoiceListItem:
    """Map Invoice ORM (with order loaded) to list schema."""
    order_ref = invoice.order.order_id if invoice.order else None
    paid = getattr(invoice, "paid_amount", None) or Decimal("0")
    if not isinstance(credit_total, Decimal):
        credit_total = Decimal(str(credit_total))
    refund_row = refund_row or {}
    r_amt = refund_row.get("refunded_amount", 0)
    if not isinstance(r_amt, Decimal):
        r_amt = Decimal(str(r_amt))
    pr_raw = refund_row.get("pending_refund_count", 0)
    pending_ref = int(pr_raw) if isinstance(pr_raw, int) else int(str(pr_raw))

    outstanding = invoice.total - credit_total - paid
    balance = quantize_currency(outstanding if outstanding > Decimal("0") else Decimal("0"))
    return InvoiceListItem(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        order_reference=order_ref,
        invoiced_date=invoice.issue_date,
        due_date=invoice.due_date,
        total=invoice.total,
        paid=paid,
        credit_applied=quantize_currency(credit_total),
        balance=balance,
        status=invoice.status,
        invoice_status=invoice.status,
        payment_status=invoice.payment_status,
        refunded_amount=quantize_currency(r_amt),
        has_pending_refunds=pending_ref > 0,
        has_open_dispute=has_open_dispute,
    )


def _invoice_payment_row_to_item(row: dict) -> InvoicePaymentHistoryItem:
    return InvoicePaymentHistoryItem(
        payment_id=str(row["payment_id"]),
        payment_number=str(row["payment_number"]),
        payment_date=row["payment_date"],
        method=str(row["method"]),
        transaction_id=row.get("provider_txn_id"),
        allocated_amount=Decimal(row.get("allocated_amount") or 0),
        status=str(row.get("status") or ""),
    )


@router.get(
    "",
    response_model=SuccessResponse[PaginatedResponse[InvoiceListItem]],
    **INVOICES_LIST,  # type: ignore[arg-type]
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_invoices(
    request: Request,
    response: Response,
    service: InvoiceServiceDep,
    _user: InvoiceReadDep,
    page: int = 1,
    size: int = 20,
    search: str | None = None,
    status: Annotated[
        list[InvoiceStatus] | None,
        Query(description="Invoice lifecycle status filter (multi-select)"),
    ] = None,
    payment_status: Annotated[
        list[PaymentStatus] | None,
        Query(description="Payment status filter (multi-select)"),
    ] = None,
    show_draft: bool = False,
    invoiced_from: date | None = None,
    invoiced_to: date | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    period: str | None = None,
    organization_id: Annotated[
        str | None,
        Query(description="Admin/SUPER_ADMIN only: narrow results to this organization (ignored for B2B; use JWT org)"),
    ] = None,
    sort_by: Annotated[
        str,
        Query(description="Sort column: issue_date, due_date, total, paid, balance, invoice_number"),
    ] = "issue_date",
    sort_order: Annotated[str, Query(description="Sort direction: asc or desc")] = "desc",
) -> dict:
    """List invoices with optional filters. Defaults: page=1, size=20, no filters."""
    org_id, customer_id = _effective_list_scope(_user, organization_id)
    items, total = await service.list_invoices(
        page=page,
        size=size,
        search=search,
        status=[s.value for s in (status or [])] or None,
        payment_status=[s.value for s in (payment_status or [])] or None,
        show_draft=show_draft,
        invoiced_from=invoiced_from,
        invoiced_to=invoiced_to,
        due_from=due_from,
        due_to=due_to,
        period=period,
        organization_id=org_id,
        customer_id=customer_id,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    ids = [inv.id for inv in items]
    rmap = await service.refund_summaries_for_invoice_ids(ids) if ids else {}
    dset = await service.invoice_ids_with_open_dispute(ids) if ids else set()
    credit_map = await service.credit_totals_for_invoice_ids(ids) if ids else {}
    list_items = [
        _invoice_to_list_item(
            inv,
            rmap.get(inv.id),
            inv.id in dset,
            credit_total=credit_map.get(str(inv.id), Decimal("0")),
        )
        for inv in items
    ]
    data = PaginatedResponse.create(list_items, total=total, page=page, size=size)
    return ok(data=data)


@router.get(
    "/summary",
    response_model=SuccessResponse[InvoiceSummaryResponse],
    **INVOICES_SUMMARY,  # type: ignore[arg-type]
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def invoices_summary(
    request: Request,
    response: Response,
    service: InvoiceServiceDep,
    _user: InvoiceReadDep,
    search: str | None = None,
    status: Annotated[
        list[InvoiceStatus] | None,
        Query(description="Invoice lifecycle status filter (multi-select)"),
    ] = None,
    payment_status: Annotated[
        list[PaymentStatus] | None,
        Query(description="Payment status filter (multi-select)"),
    ] = None,
    show_draft: bool = False,
    invoiced_from: date | None = None,
    invoiced_to: date | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    period: str | None = None,
    organization_id: Annotated[
        str | None,
        Query(description="Admin/SUPER_ADMIN only: narrow aggregates to this organization (ignored for B2B; use JWT org)"),
    ] = None,
) -> dict:
    org_id, customer_id = _effective_list_scope(_user, organization_id)
    data = await service.summary_invoices(
        search=search,
        status=[s.value for s in (status or [])] or None,
        payment_status=[s.value for s in (payment_status or [])] or None,
        show_draft=show_draft,
        invoiced_from=invoiced_from,
        invoiced_to=invoiced_to,
        due_from=due_from,
        due_to=due_to,
        period=period,
        organization_id=org_id,
        customer_id=customer_id,
    )
    return ok(data=InvoiceSummaryResponse(**data))


@router.post(
    "",
    response_model=SuccessResponse[InvoiceDetailResponse],
    status_code=status.HTTP_201_CREATED,
    **INVOICES_CREATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def create_draft(
    request: Request,
    response: Response,
    body: InvoiceCreateRequest,
    service: InvoiceServiceDep,
    _user: InvoiceWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admin can create invoices")
    if body.finalize:
        invoice = await service.create_and_finalize(
            order_id=body.order_id,
            organization_id=body.organization_id,
            customer_id=body.customer_id,
            billing_contact_email=body.billing_contact_email,
            issue_date=body.issue_date,
            due_date=body.due_date,
            subtotal=body.subtotal,
            vat_rate=body.vat_rate,
            vat_amount=body.vat_amount,
            total=body.total,
            notes=body.notes,
            audit_user_id=_user.id,
            audit_user_role=_user.role,
        )
    else:
        invoice = await service.create_draft(
            order_id=body.order_id,
            organization_id=body.organization_id,
            customer_id=body.customer_id,
            billing_contact_email=body.billing_contact_email,
            issue_date=body.issue_date,
            due_date=body.due_date,
            subtotal=body.subtotal,
            vat_rate=body.vat_rate,
            vat_amount=body.vat_amount,
            total=body.total,
            notes=body.notes,
            audit_user_id=_user.id,
            audit_user_role=_user.role,
        )
    if body.line_items:
        await service._replace_line_items(
            invoice.id,
            [
                {
                    "description": li.description,
                    "quantity": li.quantity,
                    "unit_price": li.unit_price,
                    "total_price": li.total_price,
                    "line_type": li.line_type,
                }
                for li in body.line_items
            ],
        )
    full = await service.get_invoice_detail(invoice.id, organization_id=None)
    credit_total = await service.get_credit_applied_total(invoice.id)
    detailed_invoice = full or invoice
    payment_method = await service.latest_invoice_payment_method(
        invoice_id=invoice.id,
        organization_id=detailed_invoice.organization_id,
    )
    detail = await _invoice_detail_response(
        service,
        detailed_invoice,
        credit_total=credit_total,
        payment_method=payment_method,
    )
    return ok(data=detail)


def _invoice_to_detail_response(
    invoice,
    credit_total: Decimal,
    payment_method: str | None = None,
    *,
    refund_summary: InvoiceRefundSummary | None = None,
    has_open_dispute: bool = False,
) -> InvoiceDetailResponse:
    """Build detail response from Invoice (with relations). credit_total = sum of applied credit; payment_status and outstanding use it."""
    order_ref = invoice.order.order_id if invoice.order else None
    total_after = quantize_currency(invoice.total - credit_total)
    paid = Decimal(invoice.paid_amount or 0)
    outstanding = quantize_currency(total_after - paid)
    rsum = refund_summary or _refund_summary_from_row(None)
    events = [
        InvoiceEventEntry(
            event_type=e.event_type,
            reason=e.reason,
            actor_id=e.actor_id,
            actor_role=e.actor_role,
            created_at=e.created_at,
            display_title=_invoice_event_display_title(e.event_type, e.reason),
        )
        for e in getattr(invoice, "events", []) or []
    ]
    applied = []
    for app in getattr(invoice, "credit_applications", []) or []:
        cn = getattr(app, "credit_note", None)
        applied.append(
            AppliedCreditNoteEntry(
                credit_note_id=app.credit_note_id,
                credit_note_number=cn.credit_note_number if cn else "",
                applied_amount=app.applied_amount,
                applied_at=app.applied_at,
                reason=cn.reason if cn else None,
            )
        )
    line_items = [
        InvoiceLineItemEntry(
            description=li.description,
            quantity=li.quantity,
            unit_price=li.unit_price,
            total_price=li.total_price,
            line_type=li.line_type,
        )
        for li in getattr(invoice, "line_items", []) or []
    ]
    return InvoiceDetailResponse(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        order_id=invoice.order_id,
        order_reference=order_ref,
        organization_id=invoice.organization_id,
        customer_id=invoice.customer_id,
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        subtotal=invoice.subtotal,
        vat_rate=invoice.vat_rate,
        vat_amount=invoice.vat_amount,
        total=invoice.total,
        total_after_credit=total_after,
        paid_amount=paid,
        outstanding_balance=outstanding,
        status=invoice.status,
        invoice_status=invoice.status,
        payment_status=invoice.payment_status,
        notes=InvoiceService.display_internal_note(invoice.notes),
        billing_contact_email=getattr(invoice, "billing_contact_email", None),
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
        version=invoice.version,
        amount_paid=paid,
        payment_method=payment_method,
        events=events,
        applied_credit_notes=applied,
        line_items=line_items,
        refund_summary=rsum,
        has_open_dispute=has_open_dispute,
    )


@router.get(
    "/{invoice_id}",
    response_model=SuccessResponse[InvoiceDetailResponse],
    **INVOICES_GET,  # type: ignore[arg-type]
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_invoice(
    request: Request,
    response: Response,
    invoice_id: str,
    service: InvoiceServiceDep,
    _user: InvoiceReadDep,
) -> dict:
    org_id, customer_id = _read_scope(_user)
    invoice = await service.get_invoice_detail(
        invoice_id,
        organization_id=org_id,
        customer_id=customer_id,
    )
    if invoice is None:
        raise NotFoundError(resource="invoice", id=invoice_id)
    credit_total = await service.get_credit_applied_total(invoice_id)
    payment_method = await service.latest_invoice_payment_method(
        invoice_id=invoice_id,
        organization_id=invoice.organization_id,
    )
    detail = await _invoice_detail_response(
        service,
        invoice,
        credit_total=credit_total,
        payment_method=payment_method,
    )
    return ok(data=detail)


@router.get(
    "/{invoice_id}/internal-note",
    response_model=SuccessResponse[InvoiceInternalNoteResponse],
    **INVOICES_INTERNAL_NOTE_GET,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_invoice_internal_note(
    request: Request,
    response: Response,
    invoice_id: str,
    service: InvoiceServiceDep,
    user: InvoiceReadDep,
) -> dict:
    _require_billing_admin(user)
    org_id = _org_scope(user)
    invoice = await service.get_invoice_internal_note(invoice_id, organization_id=org_id)
    return ok(data=_internal_note_response(invoice))


@router.post(
    "/{invoice_id}/internal-note",
    response_model=SuccessResponse[InvoiceInternalNoteResponse],
    status_code=status.HTTP_201_CREATED,
    **INVOICES_INTERNAL_NOTE_CREATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def create_invoice_internal_note(
    request: Request,
    response: Response,
    invoice_id: str,
    body: InvoiceInternalNoteWriteRequest,
    service: InvoiceServiceDep,
    user: InvoiceWriteDep,
) -> dict:
    _require_billing_admin(user)
    org_id = _org_scope(user)
    invoice = await service.create_invoice_internal_note(
        invoice_id,
        notes=body.notes,
        version=body.version,
        organization_id=org_id,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(data=_internal_note_response(invoice), message="Internal note created")


@router.put(
    "/{invoice_id}/internal-note",
    response_model=SuccessResponse[InvoiceInternalNoteResponse],
    **INVOICES_INTERNAL_NOTE_UPDATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def update_invoice_internal_note(
    request: Request,
    response: Response,
    invoice_id: str,
    body: InvoiceInternalNoteWriteRequest,
    service: InvoiceServiceDep,
    user: InvoiceWriteDep,
) -> dict:
    _require_billing_admin(user)
    org_id = _org_scope(user)
    invoice = await service.update_invoice_internal_note(
        invoice_id,
        notes=body.notes,
        version=body.version,
        organization_id=org_id,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(data=_internal_note_response(invoice), message="Internal note updated")


@router.delete(
    "/{invoice_id}/internal-note",
    response_model=SuccessResponse[InvoiceInternalNoteResponse],
    **INVOICES_INTERNAL_NOTE_DELETE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_invoice_internal_note(
    request: Request,
    response: Response,
    invoice_id: str,
    service: InvoiceServiceDep,
    user: InvoiceWriteDep,
    version: Annotated[int, Query(ge=1, description="Invoice version from last read (optimistic lock).")],
) -> dict:
    _require_billing_admin(user)
    org_id = _org_scope(user)
    invoice = await service.delete_invoice_internal_note(
        invoice_id,
        version=version,
        organization_id=org_id,
        audit_user_id=user.id,
        audit_user_role=user.role,
    )
    return ok(data=_internal_note_response(invoice), message="Internal note deleted")


@router.get(
    "/{invoice_id}/payments",
    response_model=SuccessResponse[PaginatedResponse[InvoicePaymentHistoryItem]],
    **INVOICES_INVOICE_PAYMENTS,  # type: ignore[arg-type]
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def invoice_payments(
    request: Request,
    response: Response,
    invoice_id: str,
    service: InvoiceServiceDep,
    _user: InvoiceReadDep,
    page: int = 1,
    size: int = 20,
) -> dict:
    org_id, customer_id = _read_scope(_user)
    invoice = await service.get_invoice_detail(
        invoice_id,
        organization_id=org_id,
        customer_id=customer_id,
    )
    if invoice is None:
        raise NotFoundError(resource="invoice", id=invoice_id)
    rows, total = await service.list_invoice_payments(
        invoice_id=invoice_id,
        page=page,
        size=size,
        organization_id=invoice.organization_id,
    )
    items = [_invoice_payment_row_to_item(row) for row in rows]
    data = PaginatedResponse.create(items, total=total, page=page, size=size)
    return ok(data=data)


@router.patch(
    "/{invoice_id}",
    response_model=SuccessResponse[InvoiceDetailResponse],
    **INVOICES_UPDATE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def update_draft(
    request: Request,
    response: Response,
    invoice_id: str,
    body: InvoiceUpdateRequest,
    service: InvoiceServiceDep,
    _user: InvoiceWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admin can update draft invoices")
    org_id = _org_scope(_user)
    data = body.model_dump(exclude_unset=True)
    updated = await service.update_draft(invoice_id, data, organization_id=org_id, audit_user_id=_user.id, audit_user_role=_user.role)
    invoice = await service.get_invoice_detail(invoice_id, organization_id=org_id)
    credit_total = await service.get_credit_applied_total(invoice_id)
    detailed_invoice = invoice or updated
    payment_method = await service.latest_invoice_payment_method(
        invoice_id=invoice_id,
        organization_id=detailed_invoice.organization_id,
    )
    detail = await _invoice_detail_response(
        service,
        detailed_invoice,
        credit_total=credit_total,
        payment_method=payment_method,
    )
    return ok(data=detail)


@router.delete(
    "/{invoice_id}",
    response_model=SuccessResponse[dict[str, bool]],
    **INVOICES_DELETE_DRAFT,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_draft_invoice(
    request: Request,
    response: Response,
    invoice_id: str,
    service: InvoiceServiceDep,
    _user: InvoiceWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admin can delete draft invoices")
    org_id = _org_scope(_user)
    await service.delete_draft(invoice_id, organization_id=org_id, audit_user_id=_user.id, audit_user_role=_user.role)
    return ok(data={"deleted": True}, message="Draft invoice deleted")


@router.post(
    "/{invoice_id}/finalize",
    response_model=SuccessResponse[InvoiceDetailResponse],
    **INVOICES_FINALIZE,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def finalize_invoice(
    request: Request,
    response: Response,
    invoice_id: str,
    service: InvoiceServiceDep,
    _user: InvoiceWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admin can finalize invoices")
    org_id = _org_scope(_user)
    await service.finalize(invoice_id, organization_id=org_id, audit_user_id=_user.id, audit_user_role=_user.role)
    invoice = await service.get_invoice_detail(invoice_id, organization_id=org_id)
    credit_total = await service.get_credit_applied_total(invoice_id)
    payment_method = await service.latest_invoice_payment_method(
        invoice_id=invoice_id,
        organization_id=invoice.organization_id if invoice is not None else org_id,
    )
    detail = await _invoice_detail_response(
        service,
        invoice,
        credit_total=credit_total,
        payment_method=payment_method,
    )
    return ok(data=detail)


@router.post(
    "/{invoice_id}/void",
    response_model=SuccessResponse[InvoiceDetailResponse],
    **INVOICES_VOID,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def void_invoice(
    request: Request,
    response: Response,
    invoice_id: str,
    body: InvoiceVoidRequest,
    service: InvoiceServiceDep,
    _user: InvoiceWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        from fastapi import HTTPException

        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admin can void invoices")
    org_id = _org_scope(_user)
    await service.void(invoice_id, reason=body.reason, organization_id=org_id, audit_user_id=_user.id, audit_user_role=_user.role)
    invoice = await service.get_invoice_detail(invoice_id, organization_id=org_id)
    credit_total = await service.get_credit_applied_total(invoice_id)
    payment_method = await service.latest_invoice_payment_method(
        invoice_id=invoice_id,
        organization_id=invoice.organization_id if invoice is not None else org_id,
    )
    detail = await _invoice_detail_response(
        service,
        invoice,
        credit_total=credit_total,
        payment_method=payment_method,
    )
    return ok(data=detail)


@router.post(
    "/{invoice_id}/write-off",
    response_model=SuccessResponse[InvoiceDetailResponse],
    **INVOICES_WRITE_OFF,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def write_off_invoice(
    request: Request,
    response: Response,
    invoice_id: str,
    body: InvoiceWriteOffRequest,
    service: InvoiceServiceDep,
    _user: InvoiceWriteDep,
) -> dict:
    if _user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admin can write off invoices")
    org_id = _org_scope(_user)
    await service.write_off(invoice_id, reason=body.reason, organization_id=org_id, audit_user_id=_user.id, audit_user_role=_user.role)
    invoice = await service.get_invoice_detail(invoice_id, organization_id=org_id)
    credit_total = await service.get_credit_applied_total(invoice_id)
    payment_method = await service.latest_invoice_payment_method(
        invoice_id=invoice_id,
        organization_id=invoice.organization_id if invoice is not None else org_id,
    )
    detail = await _invoice_detail_response(
        service,
        invoice,
        credit_total=credit_total,
        payment_method=payment_method,
    )
    return ok(data=detail)


# ── PDF: request generation (dedupe by signature), poll status, get signed URL ──
@router.post(
    "/{invoice_id}/pdf",
    response_model=SuccessResponse[PdfStatusResponse],
    **INVOICES_PDF_REQUEST,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def request_pdf(
    request: Request,
    response: Response,
    invoice_id: str,
    service: InvoiceServiceDep,
    _user: InvoiceReadDep,
    x_idempotency_key: Annotated[
        str | None,
        Header(alias="x-idempotency-key", description="Optional idempotency key for safe PDF retries"),
    ] = None,
) -> dict:
    if x_idempotency_key and len(x_idempotency_key) > 256:
        raise ValidationError("X-Idempotency-Key header max length is 256 characters")
    org_id, customer_id = _read_scope(_user)
    payload, _ = await service.request_pdf(
        invoice_id,
        organization_id=org_id,
        customer_id=customer_id,
        idempotency_key=x_idempotency_key,
    )
    return ok(data=PdfStatusResponse(**payload))


@router.get(
    "/{invoice_id}/pdf",
    response_model=SuccessResponse[PdfStatusResponse],
    **INVOICES_PDF_STATUS,  # type: ignore[arg-type]
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_pdf_status(
    request: Request,
    response: Response,
    invoice_id: str,
    service: InvoiceServiceDep,
    _user: InvoiceReadDep,
) -> dict:
    org_id, customer_id = _read_scope(_user)
    payload = await service.get_pdf_status(
        invoice_id,
        organization_id=org_id,
        customer_id=customer_id,
    )
    return ok(data=PdfStatusResponse(**payload))


@router.post(
    "/{invoice_id}/pdf/signed-url",
    response_model=SuccessResponse[SignedUrlResponse],
    **INVOICES_PDF_SIGNED_URL,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_pdf_signed_url(
    request: Request,
    response: Response,
    invoice_id: str,
    body: SignedUrlRequest,
    service: InvoiceServiceDep,
    _user: InvoiceReadDep,
) -> dict:
    # Require READY artifact; signed URL is generated on demand (not stored)
    org_id, customer_id = _read_scope(_user)
    invoice = await service.get_invoice(
        invoice_id,
        organization_id=org_id,
        customer_id=customer_id,
    )
    latest = await service.get_pdf_status(
        invoice_id,
        organization_id=org_id,
        customer_id=customer_id,
    )
    if latest.get("status") != "READY" or not latest.get("artifact_id"):
        raise NotFoundError(resource="invoice_pdf", id=invoice_id)
    from app.modules.invoices.repository import InvoicePdfArtifactRepository
    from app.modules.invoices.service import SIGNED_URL_EXPIRY_SECONDS
    from app.storage.r2_client import generate_presigned_url

    artifact_repo = InvoicePdfArtifactRepository(service._session)
    artifact = await artifact_repo.get_latest_for_invoice(invoice_id)
    if artifact is None or artifact.status != "READY" or not artifact.r2_file_key:
        raise NotFoundError(resource="invoice_pdf", id=invoice_id)
    filename_base = getattr(invoice, "invoice_number", None)
    if not filename_base:
        filename_base = f"invoice-{invoice_id}"
    safe_name = str(filename_base).replace('"', "").replace("\n", "").replace("\r", "")
    disposition = body.disposition if body.disposition in {"inline", "attachment"} else "attachment"
    content_disposition = f'{disposition}; filename="{safe_name}.pdf"'
    url = generate_presigned_url(
        artifact.r2_file_key,
        expiry_seconds=SIGNED_URL_EXPIRY_SECONDS,
        content_type="application/pdf",
        response_content_disposition=content_disposition,
    )
    expires_at = (datetime.now(UTC) + timedelta(seconds=SIGNED_URL_EXPIRY_SECONDS)).isoformat()
    return ok(data=SignedUrlResponse(url=url, expires_at=expires_at))
