"""Driver self stop execution routes."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile

from app.common.deps import Allowed, AuthUser
from app.common.enums import UserRole
from app.common.response import ok
from app.common.schemas import SuccessResponse
from app.core.rate_limit import DRIVERS_READ_RATE_LIMIT, DRIVERS_WRITE_RATE_LIMIT, limiter
from app.modules.drivers.service import DriverService
from app.modules.drivers.v1.docs import (
    SELF_STOP_COMPLETE_DELIVERY,
    SELF_STOP_NOTES,
    SELF_STOP_NOTES_ACK,
    SELF_STOP_PACKAGE_PROGRESS,
    SELF_STOP_PACKAGE_MISSING_REPORT,
    SELF_STOP_PENDING_PACKAGES,
    SELF_STOP_PACKAGE_SCAN,
    SELF_STOP_PACKAGES_BATCH_STATUS,
    SELF_STOP_PACKAGE_STATUS,
    SELF_STOP_POD_CONFIRM,
    SELF_STOP_POD_PHOTOS,
    SELF_STOP_POD_DELETE,
    SELF_STOP_POD_UPLOAD_URL,
    SELF_STOP_READINESS,
    SELF_STOP_READINESS_NOTES,
    SELF_STOP_READINESS_PACKAGES,
    SELF_STOP_READINESS_POD,
    SELF_STOP_READINESS_SIGNATURE,
    SELF_STOP_SIGNATURE,
)
from app.modules.drivers.v1.schemas import (
    DriverStopCompleteRequest,
    DriverStopCompleteResponse,
    DriverStopMissingReportRequest,
    DriverStopMissingReportResponse,
    DriverStopNotesAcknowledgeRequest,
    DriverStopNotesAcknowledgeResponse,
    DriverStopNotesResponse,
    DriverStopPackageScanRequest,
    DriverStopPackageScanResponse,
    DriverStopPackagesBatchStatusRequest,
    DriverStopPackagesBatchStatusResponse,
    DriverStopPackageProgressResponse,
    DriverStopPendingPackageEntry,
    DriverStopPendingPackagesResponse,
    DriverStopPackageStatusRequest,
    DriverStopPackageStatusResponse,
    DriverStopPodPhotoEntry,
    DriverStopPodPhotosResponse,
    DriverStopPodPhotoConfirmRequest,
    DriverStopPodPhotoConfirmResponse,
    DriverStopPodUploadUrlResponse,
    DriverStopReadinessGateNotesResponse,
    DriverStopReadinessGatePackagesResponse,
    DriverStopReadinessGatePodResponse,
    DriverStopReadinessGateSignatureResponse,
    DriverStopReadinessResponse,
    DriverStopSignatureRequest,
    DriverStopSignatureResponse,
)

router = APIRouter(
    prefix="/me/routes/{route_id}/stops/{stop_id}",
    tags=["Driver profile - stop execution"],
)

DriverServiceDep = Annotated[DriverService, Depends(DriverService.dep)]
DriverSelfDep = Annotated[AuthUser, Allowed(UserRole.DRIVER)]


@router.get("/notes", response_model=SuccessResponse[DriverStopNotesResponse], **SELF_STOP_NOTES)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_stop_notes(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.get_stop_notes_payload(route_id=route_id, stop_id=stop_id, driver_id=driver.id),
    )
    return ok(data=DriverStopNotesResponse(**payload))


@router.post("/notes/acknowledge", response_model=SuccessResponse[DriverStopNotesAcknowledgeResponse], **SELF_STOP_NOTES_ACK)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def acknowledge_stop_notes(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    body: DriverStopNotesAcknowledgeRequest,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.acknowledge_stop_notes(
        route_id=route_id,
        stop_id=stop_id,
        driver_id=driver.id,
        notes_hash=body.notes_hash,
        audit_user_id=user.id,
        audit_user_role=user.role,
        ),
    )
    return ok(data=DriverStopNotesAcknowledgeResponse(**payload))


@router.post("/packages/scan", response_model=SuccessResponse[DriverStopPackageScanResponse], **SELF_STOP_PACKAGE_SCAN)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def scan_stop_package(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    body: DriverStopPackageScanRequest,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.scan_stop_package(
        route_id=route_id,
        stop_id=stop_id,
        driver_id=driver.id,
        scan_value=body.scan_value.strip(),
        ),
    )
    return ok(data=DriverStopPackageScanResponse(**payload))


@router.post(
    "/packages/batch-status",
    response_model=SuccessResponse[DriverStopPackagesBatchStatusResponse],
    **SELF_STOP_PACKAGES_BATCH_STATUS,
)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def set_return_stop_packages_status_batch(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    body: DriverStopPackagesBatchStatusRequest,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.set_return_stop_packages_status_batch(
            route_id=route_id,
            stop_id=stop_id,
            driver_id=driver.id,
            package_ids=list(body.package_ids),
            status=body.status,
            notes=body.notes,
            audit_user_id=user.id,
            audit_user_role=user.role,
        ),
    )
    return ok(data=DriverStopPackagesBatchStatusResponse(**payload))


@router.patch("/packages/{package_id}/status", response_model=SuccessResponse[DriverStopPackageStatusResponse], **SELF_STOP_PACKAGE_STATUS)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def set_stop_package_status(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    package_id: str,
    body: DriverStopPackageStatusRequest,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.set_stop_package_status(
        route_id=route_id,
        stop_id=stop_id,
        package_id=package_id,
        driver_id=driver.id,
        status=body.status,
        notes=body.notes,
        audit_user_id=user.id,
        audit_user_role=user.role,
        ),
    )
    return ok(data=DriverStopPackageStatusResponse(**payload))


@router.post("/packages/{package_id}/missing-report", response_model=SuccessResponse[DriverStopMissingReportResponse], **SELF_STOP_PACKAGE_MISSING_REPORT)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def report_missing_package(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    package_id: str,
    body: DriverStopMissingReportRequest,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.report_missing_package(
        route_id=route_id,
        stop_id=stop_id,
        package_id=package_id,
        driver_id=driver.id,
        reason_code=body.reason_code,
        details=body.details,
        audit_user_id=user.id,
        audit_user_role=user.role,
        ),
    )
    return ok(data=DriverStopMissingReportResponse(**payload))


@router.get("/packages/pending", response_model=SuccessResponse[DriverStopPendingPackagesResponse], **SELF_STOP_PENDING_PACKAGES)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_pending_stop_packages(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.list_stop_pending_packages(
        route_id=route_id,
        stop_id=stop_id,
        driver_id=driver.id,
        ),
    )
    return ok(
        data=DriverStopPendingPackagesResponse(
            route_id=route_id,
            stop_id=stop_id,
            delivery_stop_id=str(payload["delivery_stop_id"]),
            items=[DriverStopPendingPackageEntry(**row) for row in payload.get("items", [])],
        )
    )


@router.get("/packages/progress", response_model=SuccessResponse[DriverStopPackageProgressResponse], **SELF_STOP_PACKAGE_PROGRESS)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_stop_package_progress(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.get_stop_package_progress(
            route_id=route_id,
            stop_id=stop_id,
            driver_id=driver.id,
        ),
    )
    return ok(data=DriverStopPackageProgressResponse(**payload))


@router.post("/pod/photos/upload", response_model=SuccessResponse[DriverStopPodUploadUrlResponse], **SELF_STOP_POD_UPLOAD_URL)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def create_pod_upload_url(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
    files: Annotated[list[UploadFile], File(...)],
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.create_stop_pod_upload_url(
        route_id=route_id,
        stop_id=stop_id,
        driver_id=driver.id,
        uploads=files,
        ),
    )
    return ok(data=DriverStopPodUploadUrlResponse(**payload))


@router.get("/pod/photos", response_model=SuccessResponse[DriverStopPodPhotosResponse], **SELF_STOP_POD_PHOTOS)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def list_pod_photos(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.list_stop_pod_photos(
        route_id=route_id,
        stop_id=stop_id,
        driver_id=driver.id,
        ),
    )
    return ok(
        data=DriverStopPodPhotosResponse(
            delivery_stop_id=str(payload["delivery_stop_id"]),
            photos_count=int(payload["photos_count"]),
            items=[DriverStopPodPhotoEntry(**row) for row in payload.get("items", [])],
        )
    )


@router.post("/pod/photos/confirm", response_model=SuccessResponse[DriverStopPodPhotoConfirmResponse], **SELF_STOP_POD_CONFIRM)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def confirm_pod_photo(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    body: DriverStopPodPhotoConfirmRequest,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.confirm_stop_pod_photo(
        route_id=route_id,
        stop_id=stop_id,
        driver_id=driver.id,
        image_key=body.image_key,
        audit_user_id=user.id,
        audit_user_role=user.role,
        ),
    )
    return ok(data=DriverStopPodPhotoConfirmResponse(**payload))


@router.delete("/pod/photos/{photo_id}", response_model=SuccessResponse[DriverStopPodPhotoConfirmResponse], **SELF_STOP_POD_DELETE)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def delete_pod_photo(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    photo_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.delete_stop_pod_photo(
        route_id=route_id,
        stop_id=stop_id,
        photo_id=photo_id,
        driver_id=driver.id,
        audit_user_id=user.id,
        audit_user_role=user.role,
        ),
    )
    return ok(data=DriverStopPodPhotoConfirmResponse(**payload))


@router.post("/pod/signature", response_model=SuccessResponse[DriverStopSignatureResponse], **SELF_STOP_SIGNATURE)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def save_stop_signature(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    body: DriverStopSignatureRequest,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.save_stop_signature(
        route_id=route_id,
        stop_id=stop_id,
        driver_id=driver.id,
        signature_image_key=body.signature_image_key,
        signature_required=body.signature_required,
        audit_user_id=user.id,
        audit_user_role=user.role,
        ),
    )
    return ok(data=DriverStopSignatureResponse(**payload))


@router.get("/readiness/notes", response_model=SuccessResponse[DriverStopReadinessGateNotesResponse], **SELF_STOP_READINESS_NOTES)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_stop_readiness_notes_gate(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.get_stop_readiness_gate_notes(
            route_id=route_id,
            stop_id=stop_id,
            driver_id=driver.id,
        ),
    )
    return ok(data=DriverStopReadinessGateNotesResponse(**payload))


@router.get(
    "/readiness/packages",
    response_model=SuccessResponse[DriverStopReadinessGatePackagesResponse],
    **SELF_STOP_READINESS_PACKAGES,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_stop_readiness_packages_gate(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.get_stop_readiness_gate_packages(
            route_id=route_id,
            stop_id=stop_id,
            driver_id=driver.id,
        ),
    )
    return ok(data=DriverStopReadinessGatePackagesResponse(**payload))


@router.get("/readiness/pod", response_model=SuccessResponse[DriverStopReadinessGatePodResponse], **SELF_STOP_READINESS_POD)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_stop_readiness_pod_gate(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.get_stop_readiness_gate_pod(
            route_id=route_id,
            stop_id=stop_id,
            driver_id=driver.id,
        ),
    )
    return ok(data=DriverStopReadinessGatePodResponse(**payload))


@router.get(
    "/readiness/signature",
    response_model=SuccessResponse[DriverStopReadinessGateSignatureResponse],
    **SELF_STOP_READINESS_SIGNATURE,
)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_stop_readiness_signature_gate(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.get_stop_readiness_gate_signature(
            route_id=route_id,
            stop_id=stop_id,
            driver_id=driver.id,
        ),
    )
    return ok(data=DriverStopReadinessGateSignatureResponse(**payload))


@router.get("/readiness", response_model=SuccessResponse[DriverStopReadinessResponse], **SELF_STOP_READINESS)
@limiter.limit(DRIVERS_READ_RATE_LIMIT)
async def get_stop_readiness(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.get_stop_delivery_readiness(
        route_id=route_id,
        stop_id=stop_id,
        driver_id=driver.id,
        ),
    )
    return ok(data=DriverStopReadinessResponse(**payload))


@router.post("/complete", response_model=SuccessResponse[DriverStopCompleteResponse], **SELF_STOP_COMPLETE_DELIVERY)
@limiter.limit(DRIVERS_WRITE_RATE_LIMIT)
async def complete_stop_delivery(
    request: Request,
    response: Response,
    route_id: str,
    stop_id: str,
    body: DriverStopCompleteRequest,
    driver_service: DriverServiceDep,
    user: DriverSelfDep,
) -> dict:
    driver = await driver_service.get_driver_by_user_id(user.id)
    payload = cast(
        dict[str, Any],
        await driver_service.complete_stop_delivery(
        route_id=route_id,
        stop_id=stop_id,
        driver_id=driver.id,
        notes=body.notes,
        audit_user_id=user.id,
        audit_user_role=user.role,
        ),
    )
    return ok(data=DriverStopCompleteResponse(**payload))
