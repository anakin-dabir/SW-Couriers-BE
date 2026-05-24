from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Query, Request, Response, status
from pydantic import Json

from app.common.deps import (
    DOCUMENT,
    IMAGE,
    Allowed,
    AuditCtxDep,
    AuthUser,
    DeletedUUIDList,
    SessionDep,
    UserRole,
    ValidatedFile,
    validated_upload,
)
from app.common.enums import PermissionLevel, Resource
from app.common.exceptions import NotFoundError, ValidationError
from app.common.response import ok
from app.common.schemas import MessageResponse, PaginatedResponse, SuccessResponse
from app.common.validators import validate_files_metadata_match
from app.core.rate_limit import DOC_OTP_VERIFY_RATE_LIMIT, limiter
from app.core.swagger.utils import schema_description
from app.modules.drivers.v1.schemas import RouteEventEntry
from app.modules.organizations.doc_access_scope import DocAccessScope
from app.modules.organizations.doc_access_service import DocAccessServiceDep
from app.modules.vehicles.deps import VehicleDocAccessDep
from app.modules.vehicles.service import VehicleService
from app.modules.vehicles.v1.docs import (
    ADD_DOCUMENT,
    ADD_SERVICE_RECORD,
    CHANGE_AVAILABILITY,
    CREATE_VEHICLE,
    DELETE_DEFECT,
    DELETE_DOCUMENT,
    DELETE_DRAFT,
    DELETE_IMAGE,
    DELETE_MAINTENANCE,
    DELETE_SERVICE_RECORD,
    DELETE_VEHICLE,
    GET_COMPLIANCE,
    GET_DRAFT,
    GET_FLEET_STATS,
    GET_MAINTENANCE_BY_ID,
    GET_SCHEDULE,
    GET_VEHICLE,
    GET_VEHICLE_ROUTE_NOTES,
    GET_VEHICLE_ROUTE_STOP_DETAIL,
    GET_VEHICLE_ROUTE_SUMMARY,
    LIST_DEFECTS,
    LIST_DELETED_VEHICLES,
    LIST_DOCUMENTS,
    LIST_DRAFTS,
    LIST_IMAGES,
    LIST_MAINTENANCE,
    LIST_SERVICE_RECORDS,
    LIST_VEHICLE_ROUTE_HARSH_BRAKING_EVENTS,
    LIST_VEHICLE_ROUTE_HISTORY,
    LIST_VEHICLE_ROUTE_SPEEDING_EVENTS,
    LIST_VEHICLE_ROUTE_STOPS,
    LIST_VEHICLES,
    LOG_MAINTENANCE,
    MAINTENANCE_COST_SUMMARY,
    PUBLISH_DRAFT,
    REPORT_DEFECT,
    SAVE_DRAFT,
    SEND_VEHICLE_DOC_OTP,
    UPDATE_DEFECT,
    UPDATE_DRAFT,
    UPDATE_MAINTENANCE,
    UPDATE_MILEAGE,
    UPDATE_SERVICE_RECORD,
    UPDATE_SPECS,
    UPLOAD_IMAGES,
    VERIFY_VEHICLE_DOC_OTP,
)
from app.modules.vehicles.v1.schemas import (
    AddServiceRecordRequest,
    BulkUploadFailureItem,
    ChangeAvailabilityRequest,
    ComplianceCertificateItemResponse,
    ComplianceServiceIntervalItemResponse,
    ComplianceSummaryResponse,
    ComplianceTaxItemResponse,
    CreateVehicleData,
    CreateVehicleRequest,
    CreateVehicleResponse,
    DefectListParams,
    DefectResponse,
    DeletedVehicleListItem,
    DeletedVehicleListParams,
    DeleteVehicleRequest,
    DocumentResponse,
    DraftListItem,
    DraftListParams,
    DraftVehicleData,
    FileUploadFailure,
    FleetStatsResponse,
    LogMaintenanceRequest,
    MaintenanceCostSummaryResponse,
    MaintenanceListParams,
    MaintenanceRecordResponse,
    ReportDefectRequest,
    ReportDefectUploadResponse,
    SaveDraftRequest,
    SaveDraftResponse,
    ScheduleParams,
    ScheduleResponse,
    ServiceRecordResponse,
    UpdateDefectRequest,
    UpdateDocumentMetadataRequest,
    UpdateDraftRequest,
    UpdateMaintenanceRecordRequest,
    UpdateMileageRequest,
    UpdateServiceRecordRequest,
    UpdateVehicleSpecsRequest,
    UpdateVehicleSpecsResponse,
    UploadDocumentRequest,
    VehicleDocAccessTokenResponse,
    VehicleDocOTPSendResponse,
    VehicleDocOTPVerifyRequest,
    VehicleImageUploadResponse,
    VehicleImageUrlListResponse,
    VehicleListItem,
    VehicleListParams,
    VehicleResponse,
    VehicleRouteDetailResponse,
    VehicleRouteHistoryParams,
    VehicleRouteHistoryResponse,
    VehicleRouteHistoryRow,
    VehicleRouteNotesResponse,
    VehicleRouteStopDetailResponse,
    VehicleRouteStopListRow,
    VehicleRouteStopsListParams,
    VehicleRouteStopsListResponse,
    VehicleRouteTelemetryEventsResponse,
)

logger = structlog.get_logger()

router = APIRouter()

_ADMIN_VEHICLE = (UserRole.ADMIN, UserRole.SUPER_ADMIN)
AdminVehicleReadDep = Annotated[
    AuthUser,
    Allowed(*_ADMIN_VEHICLE, resource=Resource.VEHICLE_MANAGEMENT, level=PermissionLevel.READ),
]
AdminVehicleWriteDep = Annotated[
    AuthUser,
    Allowed(*_ADMIN_VEHICLE, resource=Resource.VEHICLE_MANAGEMENT, level=PermissionLevel.WRITE),
]
VehicleServiceDep = Annotated[VehicleService, Depends(VehicleService.dep)]


# Fleet Stats


@router.get(
    "/stats",
    response_model=SuccessResponse[FleetStatsResponse],
    **GET_FLEET_STATS,
)
async def get_fleet_stats(
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
) -> dict:
    stats = await svc.get_fleet_stats()
    return ok(FleetStatsResponse(**stats))


# Drafts


@router.post(
    "/drafts",
    response_model=SaveDraftResponse,
    status_code=status.HTTP_201_CREATED,
    **SAVE_DRAFT,
)
async def save_draft(
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
    validated_images: Annotated[list[ValidatedFile], validated_upload(IMAGE, field_name="images", max_files=2, optional=True)],
    validated_documents: Annotated[list[ValidatedFile], validated_upload(DOCUMENT, field_name="documents", max_files=5, optional=True)],
    vehicle_data: Annotated[
        Json[SaveDraftRequest],
        Form(
            media_type="application/json",
            description=schema_description(SaveDraftRequest),
        ),
    ],
    documents_metadata: Annotated[
        Json[list[UploadDocumentRequest]] | None,
        Form(
            media_type="application/json",
            description=schema_description(UploadDocumentRequest, array=True),
        ),
    ] = None,
) -> dict:
    validate_files_metadata_match(validated_documents, documents_metadata, files_label="documents", metadata_label="documents_metadata")

    draft, vehicle = await svc.save_draft(vehicle_data, ctx)

    image_failures = await svc.handle_draft_image_uploads(vehicle.id, validated_images, ctx)
    doc_failures = await svc.handle_draft_document_uploads(vehicle.id, validated_documents, documents_metadata, ctx)

    all_images = await svc.get_draft_all_images(vehicle.id)
    all_docs = await svc.get_draft_all_documents(vehicle.id)

    data = await svc.draft_to_response(draft, vehicle, images=all_images, documents=all_docs)
    return ok(
        data,
        message="Draft saved successfully",
        failed_documents=doc_failures,
        failed_images=image_failures,
    )


@router.get(
    "/drafts",
    response_model=SuccessResponse[PaginatedResponse[DraftListItem]],
    **LIST_DRAFTS,
)
async def list_drafts(
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
    params: Annotated[DraftListParams, Query()],
) -> dict:
    items, total = await svc.list_drafts(
        page=params.page,
        size=params.size,
        order_desc=params.order_desc,
        search=params.search,
    )
    return ok(PaginatedResponse.create(items, total, params.page, params.size))


@router.get(
    "/drafts/{draft_id}",
    response_model=SuccessResponse[DraftVehicleData],
    **GET_DRAFT,
)
async def get_draft(
    draft_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
) -> dict:
    draft, vehicle = await svc.get_draft(draft_id)
    image_urls = await svc.get_draft_all_images(vehicle.id)
    doc_responses = await svc.get_draft_all_documents(vehicle.id)
    data = await svc.draft_to_response(draft, vehicle, images=image_urls, documents=doc_responses)
    return ok(data)


@router.patch(
    "/drafts/{draft_id}",
    response_model=SaveDraftResponse,
    **UPDATE_DRAFT,
)
async def update_draft(
    draft_id: str,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
    validated_images: Annotated[list[ValidatedFile], validated_upload(IMAGE, field_name="images", max_files=2, optional=True)],
    validated_documents: Annotated[list[ValidatedFile], validated_upload(DOCUMENT, field_name="documents", max_files=5, optional=True)],
    vehicle_data: Annotated[
        Json[UpdateDraftRequest],
        Form(
            media_type="application/json",
            description=schema_description(UpdateDraftRequest),
        ),
    ],
    documents_metadata: Annotated[
        Json[list[UploadDocumentRequest]] | None,
        Form(
            media_type="application/json",
            description=schema_description(UploadDocumentRequest, array=True),
        ),
    ] = None,
    deleted_image_ids: DeletedUUIDList = None,
    deleted_document_ids: DeletedUUIDList = None,
    updated_documents_metadata: Annotated[
        Json[list[UpdateDocumentMetadataRequest]] | None,
        Form(
            description='JSON array of metadata updates for existing documents, e.g. [{"id":"doc-id","expiry_date":"2028-01-01"}]',
        ),
    ] = None,
) -> dict:
    validate_files_metadata_match(validated_documents, documents_metadata, files_label="documents", metadata_label="documents_metadata")

    draft, vehicle = await svc.update_draft(draft_id, vehicle_data, ctx)

    if deleted_image_ids:
        await svc.delete_draft_images(vehicle.id, [str(uid) for uid in deleted_image_ids], ctx)
    if deleted_document_ids:
        await svc.delete_draft_documents(vehicle.id, [str(uid) for uid in deleted_document_ids], ctx)
    if updated_documents_metadata:
        await svc.update_draft_document_metadata(vehicle.id, updated_documents_metadata, ctx)

    image_failures = await svc.handle_draft_image_uploads(vehicle.id, validated_images, ctx)
    doc_failures = await svc.handle_draft_document_uploads(vehicle.id, validated_documents, documents_metadata, ctx)

    all_images = await svc.get_draft_all_images(vehicle.id)
    all_docs = await svc.get_draft_all_documents(vehicle.id)

    data = await svc.draft_to_response(draft, vehicle, images=all_images, documents=all_docs)
    return ok(
        data,
        message="Draft updated successfully",
        failed_documents=doc_failures,
        failed_images=image_failures,
    )


@router.delete(
    "/drafts/{draft_id}",
    response_model=MessageResponse,
    **DELETE_DRAFT,
)
async def delete_draft(
    draft_id: str,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    await svc.delete_draft(draft_id, ctx)
    return ok(message="Draft removed successfully")


@router.post(
    "/drafts/{draft_id}/publish",
    response_model=CreateVehicleResponse,
    **PUBLISH_DRAFT,
)
async def publish_draft(
    draft_id: str,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
    validated_images: Annotated[list[ValidatedFile], validated_upload(IMAGE, field_name="images", max_files=2, optional=True)],
    validated_documents: Annotated[list[ValidatedFile], validated_upload(DOCUMENT, field_name="documents", max_files=5, optional=True)],
    vehicle_data: Annotated[
        Json[UpdateDraftRequest],
        Form(
            media_type="application/json",
            description=schema_description(UpdateDraftRequest),
        ),
    ],
    documents_metadata: Annotated[
        Json[list[UploadDocumentRequest]] | None,
        Form(
            media_type="application/json",
            description=schema_description(UploadDocumentRequest, array=True),
        ),
    ] = None,
    deleted_image_ids: DeletedUUIDList = None,
    deleted_document_ids: DeletedUUIDList = None,
    updated_documents_metadata: Annotated[
        Json[list[UpdateDocumentMetadataRequest]] | None,
        Form(
            description="JSON array of metadata updates for existing documents",
        ),
    ] = None,
) -> dict:
    validate_files_metadata_match(validated_documents, documents_metadata, files_label="documents", metadata_label="documents_metadata")

    _, vehicle = await svc.require_draft_vehicle_for_publish(draft_id)
    vehicle_id = vehicle.id

    if deleted_image_ids:
        await svc.delete_draft_images(vehicle_id, [str(uid) for uid in deleted_image_ids], ctx)
    if deleted_document_ids:
        await svc.delete_draft_documents(vehicle_id, [str(uid) for uid in deleted_document_ids], ctx)
    if updated_documents_metadata:
        await svc.update_draft_document_metadata(vehicle_id, updated_documents_metadata, ctx)

    image_failures = await svc.handle_draft_image_uploads(vehicle_id, validated_images, ctx)
    doc_failures = await svc.handle_draft_document_uploads(vehicle_id, validated_documents, documents_metadata, ctx)

    result = await svc.publish_draft(draft_id, vehicle_data, ctx)

    return ok(result["data"], message=result["message"], failed_documents=doc_failures, failed_images=image_failures)


# Vehicle CRUD


@router.get(
    "/deleted",
    response_model=SuccessResponse[PaginatedResponse[DeletedVehicleListItem]],
    **LIST_DELETED_VEHICLES,
)
async def list_deleted_vehicles(
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
    params: Annotated[DeletedVehicleListParams, Query()],
) -> dict:
    rows, total = await svc.list_deleted_vehicles(page=params.page, size=params.size)
    items = [svc.deleted_vehicle_to_list_item(r) for r in rows]
    return ok(PaginatedResponse.create(items, total, params.page, params.size))


@router.get(
    "",
    response_model=SuccessResponse[PaginatedResponse[VehicleListItem]],
    **LIST_VEHICLES,
)
async def list_vehicles(
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
    params: Annotated[VehicleListParams, Query()],
) -> dict:
    items, total, defect_counts = await svc.list_vehicles(
        page=params.page,
        size=params.size,
        search=params.search,
        live_status=[s.value for s in params.status] if params.status else None,
        availability=params.availability,
        mot_status=params.mot_status,
        tax_status=params.tax_status,
    )

    response_items = []
    for v in items:
        counts = defect_counts.get(v.id, {})
        item = svc.to_vehicle_list_item(v, counts)
        response_items.append(item)

    return ok(PaginatedResponse.create(response_items, total, params.page, params.size))


@router.post(
    "",
    response_model=CreateVehicleResponse,
    status_code=status.HTTP_201_CREATED,
    **CREATE_VEHICLE,
)
async def create_vehicle(
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
    validated_images: Annotated[list[ValidatedFile], validated_upload(IMAGE, field_name="images", max_files=2, optional=True)],
    validated_documents: Annotated[list[ValidatedFile], validated_upload(DOCUMENT, field_name="documents", max_files=5, optional=True)],
    vehicle_data: Annotated[Json[CreateVehicleRequest], Form(media_type="application/json", description=schema_description(CreateVehicleRequest))],
    documents_metadata: Annotated[
        Json[list[UploadDocumentRequest]] | None, Form(media_type="application/json", description=schema_description(UploadDocumentRequest, array=True))
    ] = None,
) -> dict:
    data = vehicle_data

    docs_to_upload: list[tuple[int, UploadDocumentRequest, tuple[bytes, str, str]]] = []
    doc_failures: list[FileUploadFailure] = []
    filename_by_idx: list[str] = []

    if validated_documents:
        if documents_metadata is None:
            raise ValidationError("documents_metadata is required when document files are provided")
        if len(documents_metadata) != len(validated_documents):
            raise ValidationError(f"documents_metadata length ({len(documents_metadata)}) " f"must match documents count ({len(validated_documents)})")

        filename_by_idx = [validated[1] for validated in validated_documents]
        for idx, validated in enumerate(validated_documents):
            _content, filename, _detected_type = validated
            meta = documents_metadata[idx]
            docs_to_upload.append((idx, meta, validated))

    vehicle = await svc.create_vehicle(data, ctx)

    image_failures: list[FileUploadFailure] = []

    if validated_images:
        outcome = await svc.add_images(vehicle.id, validated_images, ctx)
        image_filename_by_idx = [validated[1] for validated in validated_images]
        for failure in outcome.failed:
            filename = image_filename_by_idx[failure.index] if failure.index < len(image_filename_by_idx) else "image"
            reason = failure.message
            if reason == "File upload failed":
                reason = "File upload failed, please retry this image"
            image_failures.append(FileUploadFailure(index=failure.index, filename=filename, reason=reason))
            logger.error(
                "vehicle.image_upload_failed",
                vehicle_id=vehicle.id,
                index=failure.index,
                filename=filename,
                reason=reason,
            )

    doc_responses: list[DocumentResponse] = []
    if docs_to_upload:
        outcome = await svc.add_documents_bulk(vehicle.id, docs_to_upload, ctx)
        doc_responses = [svc.document_to_response(d) for d in outcome.created]
        for failure in outcome.failed:
            reason = failure.message
            if reason == "File upload failed":
                reason = "File upload failed, please retry this document"

            filename = filename_by_idx[failure.index] if failure.index < len(filename_by_idx) else "document"
            doc_failures.append(FileUploadFailure(index=failure.index, filename=filename, reason=reason))
            logger.error(
                "vehicle.document_upload_failed",
                vehicle_id=vehicle.id,
                index=failure.index,
                filename=filename,
                reason=reason,
            )

    total_docs = len(doc_responses) + len(doc_failures)
    if doc_failures and not doc_responses and total_docs > 0:
        msg = f"Vehicle registered but all {len(doc_failures)} document(s) failed to upload"
    elif doc_failures:
        msg = f"Vehicle registered — {len(doc_responses)} document(s) uploaded, {len(doc_failures)} failed"
    else:
        msg = "Vehicle registered successfully"

    image_items = await svc.get_draft_all_images(vehicle.id)
    vehicle_payload = await svc.to_vehicle_response(vehicle, image_items)
    response_data = CreateVehicleData(**vehicle_payload.model_dump(), documents=doc_responses)
    return ok(response_data, message=msg, failed_documents=doc_failures, failed_images=image_failures)


@router.get(
    "/{vehicle_id}",
    response_model=SuccessResponse[VehicleResponse],
    **GET_VEHICLE,
)
async def get_vehicle(
    vehicle_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
) -> dict:
    vehicle, images = await svc.get_vehicle_with_image_urls(vehicle_id)
    return ok(await svc.to_vehicle_response(vehicle, images))


@router.patch(
    "/{vehicle_id}/specs",
    response_model=UpdateVehicleSpecsResponse,
    **UPDATE_SPECS,
)
async def update_vehicle_specs(
    vehicle_id: str,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
    validated_images: Annotated[list[ValidatedFile], validated_upload(IMAGE, field_name="images", max_files=2, optional=True)],
    vehicle_data: Annotated[
        Json[UpdateVehicleSpecsRequest],
        Form(
            media_type="application/json",
            description=schema_description(UpdateVehicleSpecsRequest),
        ),
    ],
    deleted_image_ids: DeletedUUIDList = None,
) -> dict:
    vehicle = await svc.update_specs(vehicle_id, vehicle_data, ctx)

    if deleted_image_ids:
        for uid in deleted_image_ids:
            await svc.delete_image(vehicle_id, str(uid), ctx)

    image_failures = await svc.handle_draft_image_uploads(vehicle_id, validated_images, ctx)
    for f in image_failures:
        logger.error(
            "vehicle.specs_image_upload_failed",
            vehicle_id=vehicle_id,
            index=f.index,
            filename=f.filename,
            reason=f.reason,
        )

    vehicle_refreshed, image_urls = await svc.get_vehicle_with_image_urls(vehicle_id)
    payload = await svc.to_vehicle_response(vehicle_refreshed, image_urls)
    return ok(payload, message="Specifications updated", failed_images=image_failures)


@router.patch(
    "/{vehicle_id}/mileage",
    response_model=SuccessResponse[VehicleResponse],
    **UPDATE_MILEAGE,
)
async def update_vehicle_mileage(
    vehicle_id: str,
    data: UpdateMileageRequest,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    vehicle = await svc.update_mileage(vehicle_id, data, ctx)
    return ok(await svc.to_vehicle_response(vehicle), message="Mileage updated")


@router.patch(
    "/{vehicle_id}/availability",
    response_model=SuccessResponse[VehicleResponse],
    **CHANGE_AVAILABILITY,
)
async def change_vehicle_availability(
    vehicle_id: str,
    data: ChangeAvailabilityRequest,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    vehicle = await svc.change_availability(vehicle_id, data, ctx)
    return ok(await svc.to_vehicle_response(vehicle), message="Availability updated")


@router.get(
    "/{vehicle_id}/schedule",
    response_model=SuccessResponse[ScheduleResponse],
    **GET_SCHEDULE,
)
async def get_vehicle_schedule(
    vehicle_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
    params: Annotated[ScheduleParams, Query()],
) -> dict:
    result = await svc.get_schedule(vehicle_id, params.start_date, params.end_date, event_types=params.event_types)
    return ok(result)


@router.get(
    "/{vehicle_id}/route-history",
    response_model=SuccessResponse[VehicleRouteHistoryResponse],
    **LIST_VEHICLE_ROUTE_HISTORY,
)
async def list_vehicle_route_history(
    vehicle_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
    params: Annotated[VehicleRouteHistoryParams, Query()],
) -> dict:
    rows, total = await svc.list_vehicle_routes_history(
        vehicle_id,
        page=params.page,
        size=params.size,
        route_type=[t.value for t in (params.type or [])] or None,
        search=params.search,
    )
    table = PaginatedResponse.create(
        items=[VehicleRouteHistoryRow(**r) for r in rows],
        total=total,
        page=params.page,
        size=params.size,
    )
    return ok(VehicleRouteHistoryResponse(table=table))


@router.get(
    "/{vehicle_id}/routes/{route_id}/summary",
    response_model=SuccessResponse[VehicleRouteDetailResponse],
    **GET_VEHICLE_ROUTE_SUMMARY,
)
async def get_vehicle_route_summary(
    vehicle_id: str,
    route_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
) -> dict:
    payload = await svc.get_vehicle_route_detail(vehicle_id, route_id)
    return ok(VehicleRouteDetailResponse(**payload))


@router.get(
    "/{vehicle_id}/routes/{route_id}/stops",
    response_model=SuccessResponse[VehicleRouteStopsListResponse],
    **LIST_VEHICLE_ROUTE_STOPS,
)
async def list_vehicle_route_stops(
    vehicle_id: str,
    route_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
    params: Annotated[VehicleRouteStopsListParams, Query()],
) -> dict:
    rows, total = await svc.list_vehicle_route_stops(vehicle_id, route_id, page=params.page, size=params.size)
    table = PaginatedResponse.create(
        items=[VehicleRouteStopListRow(**r) for r in rows],
        total=total,
        page=params.page,
        size=params.size,
    )
    return ok(VehicleRouteStopsListResponse(table=table))


@router.get(
    "/{vehicle_id}/routes/{route_id}/stops/{route_stop_id}",
    response_model=SuccessResponse[VehicleRouteStopDetailResponse],
    **GET_VEHICLE_ROUTE_STOP_DETAIL,
)
async def get_vehicle_route_stop_detail(
    vehicle_id: str,
    route_id: str,
    route_stop_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
) -> dict:
    payload = await svc.get_vehicle_route_stop_detail(vehicle_id, route_id, route_stop_id)
    return ok(VehicleRouteStopDetailResponse(**payload))


@router.get(
    "/{vehicle_id}/routes/{route_id}/telematics/speeding",
    response_model=SuccessResponse[VehicleRouteTelemetryEventsResponse],
    **LIST_VEHICLE_ROUTE_SPEEDING_EVENTS,
)
async def list_vehicle_route_speeding_events(
    vehicle_id: str,
    route_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
) -> dict:
    rows = await svc.list_vehicle_route_speeding_events(vehicle_id, route_id)
    return ok(VehicleRouteTelemetryEventsResponse(items=[RouteEventEntry(**r) for r in rows]))


@router.get(
    "/{vehicle_id}/routes/{route_id}/telematics/harsh-braking",
    response_model=SuccessResponse[VehicleRouteTelemetryEventsResponse],
    **LIST_VEHICLE_ROUTE_HARSH_BRAKING_EVENTS,
)
async def list_vehicle_route_harsh_braking_events(
    vehicle_id: str,
    route_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
) -> dict:
    rows = await svc.list_vehicle_route_harsh_braking_events(vehicle_id, route_id)
    return ok(VehicleRouteTelemetryEventsResponse(items=[RouteEventEntry(**r) for r in rows]))


@router.get(
    "/{vehicle_id}/routes/{route_id}/notes",
    response_model=SuccessResponse[VehicleRouteNotesResponse],
    **GET_VEHICLE_ROUTE_NOTES,
)
async def get_vehicle_route_notes(
    vehicle_id: str,
    route_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
) -> dict:
    payload = await svc.get_vehicle_route_notes(vehicle_id, route_id)
    return ok(VehicleRouteNotesResponse(**payload))


@router.delete(
    "/{vehicle_id}",
    response_model=MessageResponse,
    **DELETE_VEHICLE,
)
async def delete_vehicle(
    vehicle_id: str,
    data: DeleteVehicleRequest,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    await svc.delete_vehicle(vehicle_id, data.reason, ctx)
    return ok(message="Vehicle removed successfully")


# Compliance


@router.get(
    "/{vehicle_id}/compliance",
    response_model=SuccessResponse[ComplianceSummaryResponse],
    **GET_COMPLIANCE,
)
async def get_compliance_summary(
    vehicle_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
) -> dict:
    summary = await svc.get_compliance_summary(vehicle_id)
    return ok(
        ComplianceSummaryResponse(
            mot=ComplianceCertificateItemResponse(**summary["mot"]),
            tax=ComplianceTaxItemResponse(**summary["tax"]),
            insurance=ComplianceCertificateItemResponse(**summary["insurance"]),
            service_interval=ComplianceServiceIntervalItemResponse(**summary["service_interval"]),
        )
    )


# Maintenance


@router.post(
    "/{vehicle_id}/maintenance",
    response_model=SuccessResponse[MaintenanceRecordResponse],
    status_code=status.HTTP_201_CREATED,
    **LOG_MAINTENANCE,
)
async def log_maintenance(
    vehicle_id: str,
    data: LogMaintenanceRequest,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    record = await svc.log_maintenance(vehicle_id, data, ctx)
    return ok(MaintenanceRecordResponse.model_validate(record), message="Maintenance record saved")


@router.get(
    "/{vehicle_id}/maintenance",
    response_model=SuccessResponse[PaginatedResponse[MaintenanceRecordResponse]],
    **LIST_MAINTENANCE,
)
async def list_maintenance(
    vehicle_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
    params: Annotated[MaintenanceListParams, Query()],
) -> dict:
    items, total = await svc.get_maintenance_records(
        vehicle_id,
        page=params.page,
        size=params.size,
        maintenance_types=params.maintenance_type,
        search=params.search,
    )
    response_items = [MaintenanceRecordResponse.model_validate(r) for r in items]
    return ok(PaginatedResponse.create(response_items, total, params.page, params.size))


@router.get(
    "/{vehicle_id}/maintenance/cost-summary",
    response_model=SuccessResponse[MaintenanceCostSummaryResponse],
    **MAINTENANCE_COST_SUMMARY,
)
async def get_maintenance_cost_summary(
    vehicle_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
) -> dict:
    summary = await svc.get_maintenance_cost_summary(vehicle_id)
    return ok(MaintenanceCostSummaryResponse(**summary))


@router.get(
    "/{vehicle_id}/maintenance/{record_id}",
    response_model=SuccessResponse[MaintenanceRecordResponse],
    **GET_MAINTENANCE_BY_ID,
)
async def get_maintenance_record(
    vehicle_id: str,
    record_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
) -> dict:
    record = await svc.get_maintenance_record(vehicle_id, record_id)
    return ok(MaintenanceRecordResponse.model_validate(record))


@router.patch(
    "/{vehicle_id}/maintenance/{record_id}",
    response_model=SuccessResponse[MaintenanceRecordResponse],
    **UPDATE_MAINTENANCE,
)
async def update_maintenance_record(
    vehicle_id: str,
    record_id: str,
    data: UpdateMaintenanceRecordRequest,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    record = await svc.update_maintenance_record(vehicle_id, record_id, data, ctx)
    return ok(MaintenanceRecordResponse.model_validate(record), message="Maintenance record updated")


@router.delete(
    "/{vehicle_id}/maintenance/{record_id}",
    response_model=MessageResponse,
    **DELETE_MAINTENANCE,
)
async def delete_maintenance_record(
    vehicle_id: str,
    record_id: str,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    await svc.delete_maintenance_record(vehicle_id, record_id, ctx)
    return ok(message="Maintenance record removed")


# Defects


@router.post(
    "/{vehicle_id}/defects",
    response_model=ReportDefectUploadResponse,
    status_code=status.HTTP_201_CREATED,
    **REPORT_DEFECT,
)
async def report_defect(
    vehicle_id: str,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
    validated_images: Annotated[list[ValidatedFile], validated_upload(IMAGE, field_name="images", max_files=10, optional=True)],
    defect_data: Annotated[
        Json[ReportDefectRequest],
        Form(
            media_type="application/json",
            description=schema_description(ReportDefectRequest),
        ),
    ],
) -> dict:
    defect_payload, upload_failures = await svc.report_defect(vehicle_id, defect_data, ctx, validated_images or None)
    failed_images = [BulkUploadFailureItem(index=f.index, message=f.message) for f in upload_failures]
    msg = "Defect reported successfully" if not failed_images else f"Defect reported; {len(failed_images)} image(s) failed to upload"
    return ok(defect_payload, message=msg, failed_images=failed_images)


@router.get(
    "/{vehicle_id}/defects",
    response_model=SuccessResponse[PaginatedResponse[DefectResponse]],
    **LIST_DEFECTS,
)
async def list_defects(
    vehicle_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
    params: Annotated[DefectListParams, Query()],
) -> dict:
    items, total = await svc.get_defects(
        vehicle_id,
        page=params.page,
        size=params.size,
        statuses=params.status,
        search=params.search,
    )
    response_items = [svc.defect_to_response(d) for d in items]
    return ok(PaginatedResponse.create(response_items, total, params.page, params.size))


@router.patch(
    "/{vehicle_id}/defects/{defect_id}",
    response_model=SuccessResponse[DefectResponse],
    **UPDATE_DEFECT,
)
async def update_defect(
    vehicle_id: str,
    defect_id: str,
    data: UpdateDefectRequest,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    defect = await svc.update_defect(vehicle_id, defect_id, data, ctx)
    return ok(defect, message="Defect updated")


@router.delete(
    "/{vehicle_id}/defects/{defect_id}",
    response_model=MessageResponse,
    **DELETE_DEFECT,
)
async def delete_defect(
    vehicle_id: str,
    defect_id: str,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    await svc.delete_defect(vehicle_id, defect_id, ctx)
    return ok(message="Defect removed")


# Service Records


@router.post(
    "/{vehicle_id}/services",
    response_model=SuccessResponse[ServiceRecordResponse],
    status_code=status.HTTP_201_CREATED,
    **ADD_SERVICE_RECORD,
)
async def add_service_record(
    vehicle_id: str,
    data: AddServiceRecordRequest,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    record = await svc.add_service_record(vehicle_id, data, ctx)
    return ok(ServiceRecordResponse.model_validate(record), message="Service record saved")


@router.get(
    "/{vehicle_id}/services",
    response_model=SuccessResponse[PaginatedResponse[ServiceRecordResponse]],
    **LIST_SERVICE_RECORDS,
)
async def list_service_records(
    vehicle_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> dict:
    items, total = await svc.get_service_records(vehicle_id, page=page, size=size)
    response_items = [ServiceRecordResponse.model_validate(r) for r in items]
    return ok(PaginatedResponse.create(response_items, total, page, size))


@router.patch(
    "/{vehicle_id}/services/{record_id}",
    response_model=SuccessResponse[ServiceRecordResponse],
    **UPDATE_SERVICE_RECORD,
)
async def update_service_record(
    vehicle_id: str,
    record_id: str,
    data: UpdateServiceRecordRequest,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    record = await svc.update_service_record(vehicle_id, record_id, data, ctx)
    return ok(ServiceRecordResponse.model_validate(record), message="Service record updated")


@router.delete(
    "/{vehicle_id}/services/{record_id}",
    response_model=MessageResponse,
    **DELETE_SERVICE_RECORD,
)
async def delete_service_record(
    vehicle_id: str,
    record_id: str,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    await svc.delete_service_record(vehicle_id, record_id, ctx)
    return ok(message="Service record removed")


# Documents


@router.post(
    "/documents/otp/send",
    response_model=SuccessResponse[VehicleDocOTPSendResponse],
    **SEND_VEHICLE_DOC_OTP,
)
async def send_vehicle_doc_otp(
    user: AdminVehicleWriteDep,
    session: SessionDep,
    service: DocAccessServiceDep,
) -> dict:
    from sqlalchemy import select as sa_select

    from app.modules.user.models import User

    stmt = sa_select(User.email, User.first_name, User.last_name).where(User.id == user.id)
    result = await session.execute(stmt)
    row = result.one_or_none()
    if row is None:
        raise NotFoundError(resource="User account")

    user_email, first_name, last_name = row
    user_name = f"{first_name or ''} {last_name or ''}".strip() or user_email

    await service.send_otp(
        user_id=user.id,
        user_email=user_email,
        user_name=user_name,
        access_scope=DocAccessScope.VEHICLE_DOCUMENTS,
    )
    return ok(VehicleDocOTPSendResponse())


@router.post(
    "/documents/otp/verify",
    response_model=SuccessResponse[VehicleDocAccessTokenResponse],
    **VERIFY_VEHICLE_DOC_OTP,
)
@limiter.limit(DOC_OTP_VERIFY_RATE_LIMIT)
async def verify_vehicle_doc_otp(
    request: Request,
    response: Response,
    body: VehicleDocOTPVerifyRequest,
    user: AdminVehicleWriteDep,
    service: DocAccessServiceDep,
) -> dict:
    result = await service.verify_otp(
        user_id=user.id,
        otp_code=body.otp,
        access_scope=DocAccessScope.VEHICLE_DOCUMENTS,
    )
    token_preview = result["doc_access_token"][:8]
    return ok(
        VehicleDocAccessTokenResponse(
            vehicle_doc_access_token=result["doc_access_token"],
            expires_in=result["expires_in"],
            expires_at=result["expires_at"],
            message=(
                f"OTP verified. Use `X-Vehicle-Doc-Access-Token: {token_preview}...` "
                "when listing or deleting vehicle documents (GET list and DELETE by id). "
                "Valid for 1 hour."
            ),
        )
    )


@router.post(
    "/{vehicle_id}/documents",
    response_model=SuccessResponse[DocumentResponse],
    status_code=status.HTTP_201_CREATED,
    **ADD_DOCUMENT,
)
async def add_document(
    vehicle_id: str,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
    doc_file: Annotated[ValidatedFile, validated_upload(DOCUMENT)],
    metadata: Annotated[
        Json[UploadDocumentRequest],
        Form(
            media_type="application/json",
            description=schema_description(UploadDocumentRequest),
        ),
    ],
) -> dict:
    document = await svc.add_document(vehicle_id, metadata, doc_file, ctx)
    return ok(svc.document_to_response(document), message="")


@router.get(
    "/{vehicle_id}/documents",
    response_model=SuccessResponse[list[DocumentResponse]],
    **LIST_DOCUMENTS,
)
async def list_documents(
    vehicle_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
    _vehicle_doc_access: VehicleDocAccessDep,
) -> dict:
    docs = await svc.get_documents(vehicle_id)
    return ok([svc.document_to_response(d) for d in docs])


@router.delete(
    "/{vehicle_id}/documents/{document_id}",
    response_model=MessageResponse,
    **DELETE_DOCUMENT,
)
async def delete_document(
    vehicle_id: str,
    document_id: str,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
    _vehicle_doc_access: VehicleDocAccessDep,
) -> dict:
    await svc.delete_document(vehicle_id, document_id, ctx)
    return ok(message="Document removed")


# Vehicle Images


@router.get(
    "/{vehicle_id}/images",
    response_model=VehicleImageUrlListResponse,
    **LIST_IMAGES,
)
async def list_images(
    vehicle_id: str,
    user: AdminVehicleReadDep,
    svc: VehicleServiceDep,
) -> dict:
    urls = await svc.list_vehicle_image_urls(vehicle_id)
    return ok(urls)


@router.post(
    "/{vehicle_id}/images",
    response_model=VehicleImageUploadResponse,
    **UPLOAD_IMAGES,
)
async def upload_images(
    vehicle_id: str,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
    validated_files: Annotated[list[ValidatedFile], validated_upload(IMAGE, max_files=10, field_name="images")],
) -> dict:
    outcome = await svc.add_images(vehicle_id, validated_files, ctx)
    failed_images = [BulkUploadFailureItem(index=f.index, message=f.message) for f in outcome.failed]
    msg = f"{len(outcome.urls)} image(s) uploaded; {len(outcome.failed)} failed" if outcome.failed else f"{len(outcome.urls)} image(s) uploaded successfully"
    return ok(outcome.urls, message=msg, failed_images=failed_images)


@router.delete(
    "/{vehicle_id}/images/{image_id}",
    response_model=MessageResponse,
    **DELETE_IMAGE,
)
async def delete_image(
    vehicle_id: str,
    image_id: str,
    user: AdminVehicleWriteDep,
    svc: VehicleServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    await svc.delete_image(vehicle_id, image_id, ctx)
    return ok(message="Image removed")
