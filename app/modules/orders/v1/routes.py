from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Query, status
from pydantic import Json

from app.common.deps import IMAGE, Allowed, AuditCtxDep, AuthUser, DeletedUUIDList, ValidatedFile, validated_upload
from app.common.enums import UserRole
from app.common.response import ok
from app.common.schemas import PaginatedResponse, SuccessResponse, quantize_currency
from app.core.swagger.utils import schema_description
from app.modules.orders.enums import MAX_RETURN_EVIDENCE_IMAGES, ClientTypeEnum
from app.modules.orders.service import OrderService
from app.modules.orders.v1.docs import (
    FAILED_DELIVERIES_LIST,
    FAILED_DELIVERIES_SUMMARY,
    ORDER_CANCEL,
    ORDER_DELIVERY_STOP_TIMELINE,
    ORDER_DRAFT_DELETE,
    ORDER_DRAFT_GET,
    ORDER_DRAFT_SUBMIT,
    ORDER_DRAFT_UPDATE,
    ORDER_DRAFTS_LIST,
    ORDER_PACKAGE_TIMELINE,
    ORDER_STOP_NOTE_CREATE,
    ORDER_STOP_NOTE_DELETE,
    ORDER_STOP_NOTE_UPDATE,
    ORDER_STOP_NOTES_LIST,
    ORDERS_CREATE,
    ORDERS_GET,
    ORDERS_GET_BY_MASTER_LABEL,
    ORDERS_LIST,
    ORDERS_PRICE_BREAKDOWN,
    ORDERS_SAVE_DRAFT,
    ORDERS_SUMMARY,
    ORDERS_TIMELINE,
    PACKAGE_INITIATE_RETURN,
    PACKAGE_MARK_AS_FOUND,
    PACKAGE_RESCHEDULE,
    PACKAGE_RESOLVE_RETURN,
    RETURNS_LIST,
    RETURNS_SUMMARY,
    STOP_CANCEL,
    STOP_INITIATE_RETURN,
    STOP_MARK_AS_FOUND,
    STOP_PACKAGES_UPDATE,
    STOP_RESCHEDULE,
    STOP_RESOLVE_RETURN,
)
from app.modules.orders.v1.schemas import (
    CreatedByEntry,
    DeliveryStopCancelResponse,
    DeliveryStopDetailResponse,
    DeliveryStopTimelineSlice,
    UpdateStopDetailsRequest,
    UpdateStopPreferencesRequest,
    UpdateStopServiceTierRequest,
    DraftListItem,
    DraftListParams,
    DraftResponse,
    FailedDeliveriesSummaryResponse,
    FailedDeliveryListParams,
    FailedDeliveryStopItem,
    OrderCancelRequest,
    OrderCancelResponse,
    OrderCreateRequest,
    OrderDetailResponse,
    OrderDraftPayload,
    OrderLabelsResponse,
    OrderClientEntry,
    OrderListItem,
    OrderListParams,
    OrderPriceBreakdownRequest,
    OrderPriceBreakdownResponse,
    OrderSummaryResponse,
    OrderTimelineResponse,
    PackageActionResponse,
    PackageTimelineSlice,
    RescheduleStopRequest,
    ResolveReturnRequest,
    ResolveReturnResponse,
    ReturnListParams,
    ReturnsSummaryResponse,
    ReturnStopItem,
    StopActionResponse,
    StopNoteCreateRequest,
    StopNoteEntry,
    StopNotesResponse,
    StopNoteUpdateRequest,
    SummaryDateRangeParams,
    UpdateStopPackagesRequest,
    UpdateStopPackagesResponse,
    validate_create_order_for_actor,
)

router = APIRouter()

OrderServiceDep = Annotated[OrderService, Depends(OrderService.dep)]
OrderUserDep = Annotated[
    AuthUser,
    Allowed(
        UserRole.SUPER_ADMIN,
        UserRole.ADMIN,
        UserRole.CUSTOMER_B2B,
        UserRole.CUSTOMER_B2C,
        UserRole.WAREHOUSE_STAFF,
    ),
]


def _require_org(user: AuthUser) -> str:
    if not user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organisation context is required",
        )
    return user.organization_id


def _resolve_org_scope(user: AuthUser, requested_organization_id: str | None = None) -> None:
    is_privileged = user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)
    if is_privileged:
        return
    scoped_org = _require_org(user)
    if requested_organization_id and requested_organization_id != scoped_org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access another organisation",
        )


def _effective_organization_id(user: AuthUser, requested_organization_id: str | None) -> str:
    is_privileged = user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)
    if is_privileged:
        if requested_organization_id:
            return requested_organization_id

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="organization_id is required for admin requests",
        )
    if requested_organization_id:
        return requested_organization_id
    return _require_org(user)


def _effective_organization_id_for_list(
    user: AuthUser,
    requested_organization_id: str | None,
) -> str | None:
    is_privileged = user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)
    if is_privileged:
        return requested_organization_id
    if requested_organization_id:
        return requested_organization_id
    return _require_org(user)


@router.get(
    "",
    response_model=SuccessResponse[PaginatedResponse[OrderListItem]],
    **ORDERS_LIST,
)
async def list_orders(
    service: OrderServiceDep,
    user: OrderUserDep,
    params: Annotated[OrderListParams, Query()],
) -> dict:
    _resolve_org_scope(user, params.organization_id)
    organization_id = _effective_organization_id_for_list(user, params.organization_id)
    orders, total = await service.list_orders(
        organization_id=organization_id,
        statuses=[s.value for s in params.status] if params.status else None,
        search=params.search,
        date_from=params.date_from,
        date_to=params.date_to,
        offset=params.offset,
        limit=params.size,
    )
    items: list[OrderListItem] = []
    for row in orders:
        created_by_name = (row.get("created_by_name") or "").strip()
        created_by = (
            CreatedByEntry(
                id=row["created_by_id"],
                name=created_by_name,
            )
            if row.get("created_by_id") and created_by_name
            else None
        )
        postcode_raw = row.get("pickup_postcode")
        pickup_postcode = (str(postcode_raw).strip() if postcode_raw is not None else "") or None
        customer_id = row.get("customer_id")
        if customer_id:
            customer_name_raw = row.get("customer_name")
            customer_name = (customer_name_raw.strip() if isinstance(customer_name_raw, str) else None) or None
            client = OrderClientEntry(
                id=customer_id,
                name=customer_name,
                reference=None,
                type=ClientTypeEnum.B2C
            )
        else:

            client_name_raw = row.get("client_name")
            client_name = client_name_raw.strip() if isinstance(client_name_raw, str) else None
            client_reference_raw = row.get("client_reference")
            client_reference = (
                client_reference_raw.strip() if isinstance(client_reference_raw, str) else None
            ) or None
            client = OrderClientEntry(
                id=row["organization_id"],
                name=client_name or None,
                reference=client_reference,
                type=ClientTypeEnum.B2B,
            )
        items.append(
            OrderListItem(
                id=row["id"],
                order_id=row["order_id"],
                organization_id=row["organization_id"],
                client=client,
                pickup_address_id=row["pickup_address_id"],
                contact_name=row["contact_name"] or None,
                pickup_address=row["pickup_address"],
                pickup_postcode=pickup_postcode,
                total_amount=quantize_currency(row.get("total_amount") or 0),
                created_by=created_by,
                status=row["status"],
                package_count=int(row["package_count"] or 0),
                delivery_stop_count=int(row["delivery_stop_count"] or 0),
                created_at=row["created_at"],
            )
        )
    return ok(PaginatedResponse.create(items, total, params.page, params.size))


@router.get(
    "/summary",
    response_model=SuccessResponse[OrderSummaryResponse],
    **ORDERS_SUMMARY,
)
async def get_orders_summary(
    service: OrderServiceDep,
    user: OrderUserDep,
    params: Annotated[SummaryDateRangeParams, Query()],
) -> dict:
    _resolve_org_scope(user, params.organization_id)
    organization_id = _effective_organization_id_for_list(user, params.organization_id)
    result = await service.get_order_summary(organization_id, params)
    return ok(data=service.order_summary_to_response(result))


@router.get(
    "/failed-deliveries/summary",
    response_model=SuccessResponse[FailedDeliveriesSummaryResponse],
    **FAILED_DELIVERIES_SUMMARY,
)
async def get_failed_deliveries_summary(
    service: OrderServiceDep,
    user: OrderUserDep,
    params: Annotated[SummaryDateRangeParams, Query()],
) -> dict:
    _resolve_org_scope(user, params.organization_id)
    organization_id = _effective_organization_id_for_list(user, params.organization_id)
    summary = await service.get_failed_deliveries_summary(organization_id, params)
    return ok(data=service.failed_deliveries_summary_to_response(summary))


@router.get(
    "/failed-deliveries",
    response_model=SuccessResponse[PaginatedResponse[FailedDeliveryStopItem]],
    **FAILED_DELIVERIES_LIST,
)
async def list_failed_deliveries(
    service: OrderServiceDep,
    user: OrderUserDep,
    params: Annotated[FailedDeliveryListParams, Query()],
) -> dict:
    _resolve_org_scope(user, params.organization_id)
    organization_id = _effective_organization_id_for_list(user, params.organization_id)
    rows, total = await service.list_failed_deliveries(
        organization_id,
        package_statuses=params.package_status,
        attempt_numbers=params.attempt_number,
        search=params.search,
        date_from=params.date_from,
        date_to=params.date_to,
        offset=params.offset,
        limit=params.size,
    )
    items = [service.failed_delivery_stop_to_item(r) for r in rows]
    return ok(PaginatedResponse.create(items, total, params.page, params.size))


@router.get(
    "/returns/summary",
    response_model=SuccessResponse[ReturnsSummaryResponse],
    **RETURNS_SUMMARY,
)
async def get_returns_summary(
    service: OrderServiceDep,
    user: OrderUserDep,
    params: Annotated[SummaryDateRangeParams, Query()],
) -> dict:
    _resolve_org_scope(user, params.organization_id)
    organization_id = _effective_organization_id_for_list(user, params.organization_id)
    summary = await service.get_returns_summary(organization_id, params)
    return ok(data=service.returns_summary_to_response(summary))


@router.get(
    "/returns",
    response_model=SuccessResponse[PaginatedResponse[ReturnStopItem]],
    **RETURNS_LIST,
)
async def list_returns(
    service: OrderServiceDep,
    user: OrderUserDep,
    params: Annotated[ReturnListParams, Query()],
) -> dict:
    _resolve_org_scope(user, params.organization_id)
    organization_id = _effective_organization_id_for_list(user, params.organization_id)
    rows, total = await service.list_returns(
        organization_id,
        package_statuses=params.status,
        attempt_numbers=params.attempt_number,
        search=params.search,
        date_from=params.date_from,
        date_to=params.date_to,
        offset=params.offset,
        limit=params.size,
    )
    items = [service.return_stop_to_item(r) for r in rows]
    return ok(PaginatedResponse.create(items, total, params.page, params.size))


@router.patch(
    "/{order_id}/stops/{stop_id}/packages",
    response_model=SuccessResponse[UpdateStopPackagesResponse],
    **STOP_PACKAGES_UPDATE,
)
async def update_stop_packages(
    order_id: str,
    stop_id: str,
    body: UpdateStopPackagesRequest,
    service: OrderServiceDep,
    user: OrderUserDep,
    ctx: AuditCtxDep,
) -> dict:
    result = await service.update_stop_packages(
        order_id=order_id,
        stop_id=stop_id,
        package_updates=[p.model_dump(exclude_unset=True) for p in body.packages],
        ctx=ctx,
    )
    return ok(data=result, message="Packages updated")


@router.patch(
    "/{order_id}/stops/{stop_id}/preferences",
    response_model=SuccessResponse[dict],
)
async def update_stop_preferences_route(
    order_id: str,
    stop_id: str,
    body: UpdateStopPreferencesRequest,
    service: OrderServiceDep,
    user: OrderUserDep,
    ctx: AuditCtxDep,
) -> dict:
    stop = await service.update_stop_preferences(
        order_id=order_id,
        stop_id=stop_id,
        signature_required=body.signature_required,
        safe_place_allowed=body.safe_place_allowed,
        ctx=ctx,
    )
    return ok(
        data={
            "delivery_stop_id": stop.id,
            "signature_required": stop.signature_required,
            "safe_place_allowed": stop.safe_place_allowed,
        },
        message="Delivery preferences updated",
    )


@router.patch(
    "/{order_id}/stops/{stop_id}/service-tier",
    response_model=SuccessResponse[UpdateStopPackagesResponse],
)
async def update_stop_service_tier_route(
    order_id: str,
    stop_id: str,
    body: UpdateStopServiceTierRequest,
    service: OrderServiceDep,
    user: OrderUserDep,
    ctx: AuditCtxDep,
) -> dict:
    result = await service.update_stop_service_tier(
        order_id=order_id,
        stop_id=stop_id,
        service_tier_id=body.service_tier_id,
        ctx=ctx,
    )
    return ok(data=result, message="Service tier updated")


@router.patch(
    "/{order_id}/stops/{stop_id}",
    response_model=SuccessResponse[dict],
)
async def update_stop_details_route(
    order_id: str,
    stop_id: str,
    body: UpdateStopDetailsRequest,
    service: OrderServiceDep,
    user: OrderUserDep,
    ctx: AuditCtxDep,
) -> dict:
    stop = await service.update_stop_details(
        order_id=order_id,
        stop_id=stop_id,
        fields=body.model_dump(exclude_unset=True),
        ctx=ctx,
    )
    return ok(
        data={
            "delivery_stop_id": stop.id,
            "recipient_first_name": stop.recipient_first_name,
            "recipient_last_name": stop.recipient_last_name,
            "recipient_phone": stop.recipient_phone,
            "recipient_email": stop.recipient_email,
            "line_1": stop.line_1,
            "line_2": stop.line_2,
            "city": stop.city,
            "postcode": stop.postcode,
        },
        message="Delivery stop details updated",
    )


@router.post(
    "/{order_id}/stops/{stop_id}/reschedule",
    response_model=SuccessResponse[StopActionResponse],
    **STOP_RESCHEDULE,
)
async def reschedule_stop(
    order_id: str,
    stop_id: str,
    body: RescheduleStopRequest,
    service: OrderServiceDep,
    user: OrderUserDep,
    ctx: AuditCtxDep,
) -> dict:
    result = await service.reschedule_stop(
        stop_id,
        scheduled_for=body.scheduled_for,
        order_id=order_id,
        ctx=ctx,
    )
    return ok(data=result, message="Delivery rescheduled")


@router.post(
    "/{order_id}/packages/{package_id}/reschedule",
    response_model=SuccessResponse[PackageActionResponse],
    **PACKAGE_RESCHEDULE,
)
async def reschedule_package(
    order_id: str,
    package_id: str,
    body: RescheduleStopRequest,
    service: OrderServiceDep,
    user: OrderUserDep,
    ctx: AuditCtxDep,
) -> dict:
    result = await service.reschedule_package(
        package_id,
        scheduled_for=body.scheduled_for,
        order_id=order_id,
        ctx=ctx,
    )
    return ok(data=result, message="Package rescheduled")


@router.post(
    "/{order_id}/stops/{stop_id}/initiate-return",
    response_model=SuccessResponse[StopActionResponse],
    **STOP_INITIATE_RETURN,
)
async def initiate_stop_return(
    order_id: str,
    stop_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
    ctx: AuditCtxDep,
) -> dict:
    result = await service.initiate_stop_return(
        stop_id,
        order_id=order_id,
        ctx=ctx,
    )
    return ok(data=result, message="Returns initiated")


@router.post(
    "/{order_id}/packages/{package_id}/initiate-return",
    response_model=SuccessResponse[PackageActionResponse],
    **PACKAGE_INITIATE_RETURN,
)
async def initiate_package_return(
    order_id: str,
    package_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
    ctx: AuditCtxDep,
) -> dict:
    result = await service.initiate_package_return(
        package_id,
        order_id=order_id,
        ctx=ctx,
    )
    return ok(data=result, message="Return initiated")


@router.post(
    "/{order_id}/stops/{stop_id}/mark-as-found",
    response_model=SuccessResponse[StopActionResponse],
    **STOP_MARK_AS_FOUND,
)
async def mark_stop_as_found(
    order_id: str,
    stop_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
    ctx: AuditCtxDep,
) -> dict:
    result = await service.mark_stop_as_found(
        stop_id,
        order_id=order_id,
        ctx=ctx,
    )
    return ok(data=result, message="Packages marked as found")


@router.post(
    "/{order_id}/packages/{package_id}/mark-as-found",
    response_model=SuccessResponse[PackageActionResponse],
    **PACKAGE_MARK_AS_FOUND,
)
async def mark_package_as_found(
    order_id: str,
    package_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
    ctx: AuditCtxDep,
) -> dict:
    result = await service.mark_package_as_found(
        package_id,
        order_id=order_id,
        ctx=ctx,
    )
    return ok(data=result, message="Package marked as found")


@router.post(
    "/{order_id}/stops/{stop_id}/resolve-return",
    response_model=SuccessResponse[ResolveReturnResponse],
    **STOP_RESOLVE_RETURN,
)
async def resolve_stop_return(
    order_id: str,
    stop_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
    ctx: AuditCtxDep,
    validated_images: Annotated[
        list[ValidatedFile],
        validated_upload(
            IMAGE,
            field_name="images",
            max_files=MAX_RETURN_EVIDENCE_IMAGES,
            max_size=2 * 1024 * 1024,
            optional=True,
        ),
    ],
    resolution_data: Annotated[
        Json[ResolveReturnRequest],
        Form(
            media_type="application/json",
            description=schema_description(ResolveReturnRequest),
        ),
    ],
) -> dict:
    result = await service.resolve_stop_return(
        stop_id,
        order_id=order_id,
        resolution=resolution_data.resolution,
        return_dispatch_date=resolution_data.return_dispatch_date,
        return_cost=resolution_data.return_cost,
        waive_return_cost=resolution_data.waive_return_cost,
        return_notes=resolution_data.return_notes,
        disposal_reason=resolution_data.disposal_reason,
        resolution_notes=resolution_data.resolution_notes,
        evidence_images=validated_images or None,
        ctx=ctx,
    )
    return ok(data=result, message="Return resolved")


@router.post(
    "/{order_id}/packages/{package_id}/resolve-return",
    response_model=SuccessResponse[ResolveReturnResponse],
    **PACKAGE_RESOLVE_RETURN,
)
async def resolve_package_return(
    order_id: str,
    package_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
    ctx: AuditCtxDep,
    validated_images: Annotated[
        list[ValidatedFile],
        validated_upload(
            IMAGE,
            field_name="images",
            max_files=MAX_RETURN_EVIDENCE_IMAGES,
            max_size=2 * 1024 * 1024,
            optional=True,
        ),
    ],
    resolution_data: Annotated[
        Json[ResolveReturnRequest],
        Form(
            media_type="application/json",
            description=schema_description(ResolveReturnRequest),
        ),
    ],
) -> dict:
    result = await service.resolve_package_return(
        package_id,
        order_id=order_id,
        resolution=resolution_data.resolution,
        return_dispatch_date=resolution_data.return_dispatch_date,
        return_cost=resolution_data.return_cost,
        waive_return_cost=resolution_data.waive_return_cost,
        return_notes=resolution_data.return_notes,
        disposal_reason=resolution_data.disposal_reason,
        resolution_notes=resolution_data.resolution_notes,
        evidence_images=validated_images or None,
        ctx=ctx,
    )
    return ok(data=result, message="Return resolved")


@router.post(
    "/{order_id}/cancel",
    response_model=SuccessResponse[OrderCancelResponse],
    **ORDER_CANCEL,
)
async def cancel_order(
    order_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
    ctx: AuditCtxDep,
    organization_id: Annotated[
        str | None,
        Query(description="Organisation scope; required for admin"),
    ] = None,
    body: Annotated[OrderCancelRequest | None, Body()] = None,
) -> dict:
    _resolve_org_scope(user, organization_id)
    data = body or OrderCancelRequest()
    result = await service.cancel_order(
        order_id,
        user=user,
        query_organization_id=organization_id,
        notes=data.notes,
        ctx=ctx,
    )
    return ok(data=result, message="Order cancelled")


@router.post(
    "/{order_id}/stops/{stop_id}/cancel",
    response_model=SuccessResponse[DeliveryStopCancelResponse],
    **STOP_CANCEL,
)
async def cancel_delivery_stop(
    order_id: str,
    stop_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
    ctx: AuditCtxDep,
    organization_id: Annotated[
        str | None,
        Query(description="Organisation scope; required for admin"),
    ] = None,
    body: Annotated[OrderCancelRequest | None, Body()] = None,
) -> dict:
    _resolve_org_scope(user, organization_id)
    data = body or OrderCancelRequest()
    result = await service.cancel_delivery_stop(
        order_id,
        stop_id,
        user=user,
        query_organization_id=organization_id,
        notes=data.notes,
        ctx=ctx,
    )
    return ok(data=result, message="Delivery stop cancelled")


@router.get("/{order_id}/master-label", response_model=SuccessResponse[OrderLabelsResponse], **ORDERS_GET_BY_MASTER_LABEL)
async def get_order_master_labels(
    order_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
) -> dict:
    labels = await service.get_order_labels_response_or_404(order_id)
    return ok(data=labels)


@router.get(
    "/{order_id}/timeline",
    response_model=SuccessResponse[OrderTimelineResponse],
    **ORDERS_TIMELINE,
)
async def get_order_timeline(
    order_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
) -> dict:
    data = await service.get_order_timeline_response_or_404(order_id)
    return ok(data=data)


@router.get(
    "/{order_id}/stops/{stop_id}/detail",
    response_model=SuccessResponse[DeliveryStopDetailResponse],
    response_model_exclude_none=False,
    **ORDERS_GET,
)
async def get_delivery_stop_detail(
    order_id: str,
    stop_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
) -> dict:
    data = await service.get_delivery_stop_detail_response_or_404(order_id, stop_id)
    return ok(data=data)


@router.get(
    "/{order_id}/timeline/delivery-stops/{stop_id}",
    response_model=SuccessResponse[DeliveryStopTimelineSlice],
    **ORDER_DELIVERY_STOP_TIMELINE,
)
async def get_delivery_stop_timeline(
    order_id: str,
    stop_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
) -> dict:
    data = await service.get_delivery_stop_timeline_response_or_404(order_id, stop_id)
    return ok(data=data)


@router.get(
    "/{order_id}/timeline/packages/{package_id}",
    response_model=SuccessResponse[PackageTimelineSlice],
    **ORDER_PACKAGE_TIMELINE,
)
async def get_package_timeline(
    order_id: str,
    package_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
) -> dict:
    data = await service.get_package_timeline_response_or_404(order_id, package_id)
    return ok(data=data)


@router.post(
    "/price-breakdown",
    response_model=SuccessResponse[OrderPriceBreakdownResponse],
    **ORDERS_PRICE_BREAKDOWN,
)
async def post_order_price_breakdown(
    body: OrderPriceBreakdownRequest,
    service: OrderServiceDep,
    user: OrderUserDep,
) -> dict:
    validate_create_order_for_actor(user, body)
    data = await service.compute_order_price_breakdown(user=user, body=body)
    return ok(data=data)


@router.post(
    "",
    response_model=SuccessResponse[OrderLabelsResponse],
    status_code=status.HTTP_201_CREATED,
    **ORDERS_CREATE,
)
async def create_order(
    body: OrderCreateRequest,
    service: OrderServiceDep,
    user: OrderUserDep,
) -> dict:
    validate_create_order_for_actor(user, body)
    org_id, contact_user_id, created_by = await service.resolve_create_order_parties(user, body)
    order = await service.create_order(
        client_type=body.client_type,
        organization_id=org_id,
        contact_user_id=contact_user_id,
        created_by_id=created_by.id,
        actor=created_by,
        pickup_address_id=body.pickup_address_id,
        requested_pickup_date=body.requested_pickup_date,
        payment_method=body.payment_method,
        payment_method_id=body.payment_method_id,
        credit_card_id=body.credit_card_id,
        payment_method_nonce=body.payment_method_nonce,
        delivery_stops=body.delivery_stops,
    )
    return ok(data=await service.get_order_labels_response_or_404(order.id))


@router.post(
    "/drafts",
    response_model=SuccessResponse[DraftResponse],
    status_code=status.HTTP_201_CREATED,
    **ORDERS_SAVE_DRAFT,
)
async def save_draft(
    body: OrderDraftPayload,
    service: OrderServiceDep,
    user: OrderUserDep,
) -> dict:
    _resolve_org_scope(user, body.organization_id)
    effective_org = _effective_organization_id(user, body.organization_id)
    payload = body.model_dump(mode="json", exclude_unset=True)
    payload.setdefault("organization_id", effective_org)
    draft = await service.save_draft(
        created_by_id=user.id,
        payload=payload,
    )
    return ok(data=await service.draft_to_response(draft, caller=user))


@router.get(
    "/drafts",
    response_model=SuccessResponse[PaginatedResponse[DraftListItem]],
    **ORDER_DRAFTS_LIST,
)
async def list_drafts(
    service: OrderServiceDep,
    user: OrderUserDep,
    params: Annotated[DraftListParams, Query()],
) -> dict:
    _resolve_org_scope(user, params.organization_id)
    organization_id = _effective_organization_id_for_list(user, params.organization_id)
    drafts, total = await service.list_drafts(
        organization_id,
        search=params.search,
        date_from=params.date_from,
        date_to=params.date_to,
        offset=params.offset,
        limit=params.size,
    )
    return ok(
        PaginatedResponse.create(
            [service.draft_to_list_item(d, pickup_address=pickup_address, created_by=created_by) for d, pickup_address, created_by in drafts],
            total,
            params.page,
            params.size,
        )
    )


@router.get(
    "/drafts/{draft_id}",
    response_model=SuccessResponse[DraftResponse],
    **ORDER_DRAFT_GET,
)
async def get_draft(
    draft_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
) -> dict:
    draft = await service.get_draft_or_404(draft_id)
    return ok(data=await service.draft_to_response(draft, caller=user))


@router.patch(
    "/drafts/{draft_id}",
    response_model=SuccessResponse[DraftResponse],
    **ORDER_DRAFT_UPDATE,
)
async def update_draft(
    draft_id: str,
    body: OrderDraftPayload,
    service: OrderServiceDep,
    user: OrderUserDep,
) -> dict:
    draft = await service.update_draft(
        draft_id,
        payload=body.model_dump(mode="json", exclude_unset=True),
    )
    return ok(data=await service.draft_to_response(draft, caller=user))


@router.post(
    "/drafts/{draft_id}/submit",
    response_model=SuccessResponse[OrderDetailResponse],
    response_model_exclude_none=False,
    **ORDER_DRAFT_SUBMIT,
)
async def submit_draft(
    draft_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
    body: Annotated[OrderDraftPayload | None, Body()] = None,
) -> dict:
    payload = body.model_dump(mode="json", exclude_unset=True) if body is not None else {}
    order = await service.submit_draft(
        draft_id,
        payload=payload,
        user=user,
    )
    return ok(data=await service.get_order_detail_response_or_404(order.id))


@router.delete(
    "/drafts/{draft_id}",
    response_model=SuccessResponse[dict[str, bool]],
    **ORDER_DRAFT_DELETE,
)
async def delete_draft(
    draft_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
) -> dict:
    await service.delete_draft(draft_id)
    return ok(data={"deleted": True}, message="Draft deleted")


@router.get(
    "/detail/{order_id}",
    response_model=SuccessResponse[OrderDetailResponse],
    response_model_exclude_none=False,
    **ORDERS_GET,
)
async def get_order(
    order_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
) -> dict:
    detail = await service.get_order_detail_response_or_404(order_id)
    return ok(data=detail)


@router.get(
    "/{order_id}/stops/{stop_id}/notes",
    response_model=SuccessResponse[StopNotesResponse],
    **ORDER_STOP_NOTES_LIST,
)
async def list_stop_notes(
    order_id: str,
    stop_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
) -> dict:
    items = await service.list_stop_notes(order_id=order_id, stop_id=stop_id)
    return ok(data=StopNotesResponse(order_id=order_id, stop_id=stop_id, items=items))


@router.post(
    "/{order_id}/stops/{stop_id}/notes",
    response_model=SuccessResponse[StopNoteEntry],
    status_code=status.HTTP_201_CREATED,
    **ORDER_STOP_NOTE_CREATE,
)
async def create_stop_note(
    order_id: str,
    stop_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
    validated_images: Annotated[list[ValidatedFile], validated_upload(IMAGE, field_name="images", max_files=5, optional=True)],
    note_data: Annotated[
        Json[StopNoteCreateRequest],
        Form(
            media_type="application/json",
            description=schema_description(StopNoteCreateRequest),
        ),
    ],
) -> dict:
    note = await service.create_stop_note(
        order_id=order_id,
        stop_id=stop_id,
        note_type=note_data.note_type,
        message=note_data.message,
        is_blocking=note_data.is_blocking,
        sort_order=note_data.sort_order,
        images=validated_images or None,
        package_ids=note_data.package_ids,
    )
    return ok(data=note, message="Stop note created")


@router.patch(
    "/{order_id}/stops/{stop_id}/notes/{note_id}",
    response_model=SuccessResponse[StopNoteEntry],
    **ORDER_STOP_NOTE_UPDATE,
)
async def update_stop_note(
    order_id: str,
    stop_id: str,
    note_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
    validated_images: Annotated[list[ValidatedFile], validated_upload(IMAGE, field_name="images", max_files=5, optional=True)],
    note_data: Annotated[
        Json[StopNoteUpdateRequest],
        Form(
            media_type="application/json",
            description=schema_description(StopNoteUpdateRequest),
        ),
    ],
    deleted_image_ids: DeletedUUIDList = None,
) -> dict:
    payload = note_data.model_dump(exclude_unset=True)
    note = await service.update_stop_note(
        order_id=order_id,
        stop_id=stop_id,
        note_id=note_id,
        note_type=payload.get("note_type"),
        message=payload.get("message"),
        is_blocking=payload.get("is_blocking"),
        sort_order=payload.get("sort_order"),
        images=validated_images or None,
        deleted_image_ids=[str(uid) for uid in deleted_image_ids] if deleted_image_ids else None,
        package_ids=payload.get("package_ids"),
        update_package_ids="package_ids" in payload,
    )
    return ok(data=note, message="Stop note updated")


@router.delete(
    "/{order_id}/stops/{stop_id}/notes/{note_id}",
    response_model=SuccessResponse[dict[str, bool]],
    **ORDER_STOP_NOTE_DELETE,
)
async def delete_stop_note(
    order_id: str,
    stop_id: str,
    note_id: str,
    service: OrderServiceDep,
    user: OrderUserDep,
) -> dict:
    await service.delete_stop_note(order_id=order_id, stop_id=stop_id, note_id=note_id)
    return ok(data={"deleted": True}, message="Stop note deleted")
