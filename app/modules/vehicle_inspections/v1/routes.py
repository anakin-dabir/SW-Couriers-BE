"""Vehicle inspection routes — driver-facing."""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, status
from pydantic import Json

from app.common.deps import (
    IMAGE,
    Allowed,
    AuditCtxDep,
    AuthUser,
    UserRole,
    ValidatedFile,
    validated_upload,
)
from app.common.response import ok
from app.common.schemas import SuccessResponse
from app.core.swagger.utils import schema_description
from app.modules.drivers.service import DriverService
from app.modules.vehicle_inspections.enums import InspectionStatus
from app.modules.vehicle_inspections.service import InspectionService
from app.modules.vehicle_inspections.v1.docs import (
    CREATE_INSPECTION,
    DELETE_INSPECTION,
    GET_ASSIGNED_VEHICLE,
    GET_INSPECTION,
    GET_LATEST_TRIP_INSPECTION_STATUS,
    GET_PENDING_INSPECTION_STATUS,
    GET_INSPECTION_STATUS,
    LOOKUP_VEHICLE,
    REPORT_DEFECT,
    SIGN_INSPECTION,
)
from app.modules.vehicle_inspections.v1.schemas import (
    AssignedVehicleResponse,
    CreateInspectionRequest,
    InspectionDefectSummary,
    InspectionResponse,
    InspectionStatusResponse,
    ReportInspectionDefectRequest,
    SignInspectionRequest,
)
from app.storage.upload import generate_image_url

logger = structlog.get_logger()

router = APIRouter()

InspectionServiceDep = Annotated[InspectionService, Depends(InspectionService.dep)]
DriverServiceDep = Annotated[DriverService, Depends(DriverService.dep)]
DriverUserDep = Annotated[AuthUser, Allowed(UserRole.DRIVER)]


# Get assigned vehicle


@router.get(
    "/assigned-vehicle",
    response_model=SuccessResponse[AssignedVehicleResponse],
    **GET_ASSIGNED_VEHICLE,
)
async def get_assigned_vehicle(
    user: DriverUserDep,
    svc: InspectionServiceDep,
    driver_svc: DriverServiceDep,
) -> dict:
    driver = await driver_svc.get_driver_by_user_id(user.id)
    return ok(await svc.get_assigned_vehicle(driver))


# Lookup vehicle by plate


@router.get(
    "/lookup/{registration_number}",
    response_model=SuccessResponse[AssignedVehicleResponse],
    **LOOKUP_VEHICLE,
)
async def lookup_vehicle(
    registration_number: str,
    user: DriverUserDep,
    svc: InspectionServiceDep,
    driver_svc: DriverServiceDep,
) -> dict:
    driver = await driver_svc.get_driver_by_user_id(user.id)
    return ok(await svc.lookup_vehicle(registration_number, driver))


# Create inspection


@router.post(
    "",
    response_model=SuccessResponse[InspectionResponse],
    status_code=status.HTTP_201_CREATED,
    **CREATE_INSPECTION,
)
async def create_inspection(
    user: DriverUserDep,
    svc: InspectionServiceDep,
    driver_svc: DriverServiceDep,
    ctx: AuditCtxDep,
    data: CreateInspectionRequest,
) -> dict:
    driver = await driver_svc.get_driver_by_user_id(user.id)
    inspection = await svc.create_inspection(driver, data, ctx)
    return ok(await svc.to_response(inspection), message="Inspection started")


# Report defect


@router.post(
    "/{inspection_id}/defects",
    response_model=SuccessResponse[InspectionDefectSummary],
    status_code=status.HTTP_201_CREATED,
    **REPORT_DEFECT,
)
async def report_defect(
    inspection_id: str,
    user: DriverUserDep,
    svc: InspectionServiceDep,
    driver_svc: DriverServiceDep,
    ctx: AuditCtxDep,
    validated_images: Annotated[list[ValidatedFile], validated_upload(IMAGE, field_name="images", max_files=5, optional=True)],
    defect_data: Annotated[
        Json[ReportInspectionDefectRequest],
        Form(
            media_type="application/json",
            description=schema_description(ReportInspectionDefectRequest),
        ),
    ],
) -> dict:
    driver = await driver_svc.get_driver_by_user_id(user.id)
    defect = await svc.report_defect(
        inspection_id=inspection_id,
        driver_id=driver.id,
        data=defect_data,
        ctx=ctx,
        images=validated_images if validated_images else None,
    )

    image_urls = [generate_image_url(img.file_path) for img in (defect.images or [])]
    return ok(
        InspectionDefectSummary(
            id=defect.id,
            reference=defect.reference,
            category=defect.category,
            severity=defect.severity,
            status=defect.status,
            description=defect.description,
            allowed_to_drive=defect.allowed_to_drive,
            images=image_urls,
        ),
        message="Defect reported",
    )


# Get inspection summary


@router.get(
    "/{inspection_id}",
    response_model=SuccessResponse[InspectionResponse],
    **GET_INSPECTION,
)
async def get_inspection(
    inspection_id: str,
    user: DriverUserDep,
    svc: InspectionServiceDep,
) -> dict:
    inspection = await svc.get_inspection(inspection_id)
    return ok(await svc.to_response(inspection))


@router.delete(
    "/{inspection_id}",
    response_model=SuccessResponse[dict],
    **DELETE_INSPECTION,
)
async def delete_inspection(
    inspection_id: str,
    user: DriverUserDep,
    svc: InspectionServiceDep,
    driver_svc: DriverServiceDep,
    ctx: AuditCtxDep,
) -> dict:
    driver = await driver_svc.get_driver_by_user_id(user.id)
    await svc.delete_inspection(inspection_id, driver.id, ctx)
    return ok(message="Inspection deleted successfully")


# Sign inspection


@router.post(
    "/{inspection_id}/sign",
    response_model=SuccessResponse[InspectionResponse],
    **SIGN_INSPECTION,
)
async def sign_inspection(
    inspection_id: str,
    user: DriverUserDep,
    svc: InspectionServiceDep,
    driver_svc: DriverServiceDep,
    ctx: AuditCtxDep,
    signature: Annotated[ValidatedFile, validated_upload(IMAGE, field_name="signature")],
    sign_data: Annotated[
        Json[SignInspectionRequest],
        Form(
            media_type="application/json",
            description=schema_description(SignInspectionRequest),
        ),
    ],
) -> dict:
    driver = await driver_svc.get_driver_by_user_id(user.id)
    inspection = await svc.sign_inspection(
        inspection_id=inspection_id,
        driver_id=driver.id,
        declaration_accepted=sign_data.declaration_accepted,
        ctx=ctx,
        signature_file=signature,
    )

    resp = await svc.to_response(inspection)
    msg = "Inspection submitted — defects reported, awaiting resolution" if inspection.result == "FAIL" else "Inspection completed — vehicle marked safe"
    return ok(resp, message=msg)


_LATEST_TRIP_STATUS_MESSAGES: dict[InspectionStatus, str] = {
    InspectionStatus.COMPLETED: "Vehicle inspection completed — no defects",
    InspectionStatus.AWAITING_RESOLUTION: "Vehicle inspection submitted — defects awaiting admin resolution",
    InspectionStatus.RESOLVED: "Vehicle inspection resolved — defects cleared, you can proceed",
}


@router.get(
    "",
    response_model=SuccessResponse[InspectionStatusResponse],
    **GET_LATEST_TRIP_INSPECTION_STATUS,
)
async def get_latest_trip_inspection_status(
    user: DriverUserDep,
    svc: InspectionServiceDep,
    driver_svc: DriverServiceDep,
) -> dict:
    driver = await driver_svc.get_driver_by_user_id(user.id)
    status_response = await svc.get_latest_trip_status(driver)
    if status_response is None:
        return ok(message="Vehicle inspection not started yet")
    return ok(status_response, message=_LATEST_TRIP_STATUS_MESSAGES.get(status_response.status, "Vehicle inspection status"))


@router.get(
    "/{inspection_id}/status",
    response_model=SuccessResponse[InspectionStatusResponse],
    **GET_INSPECTION_STATUS,
)
async def get_inspection_status(
    inspection_id: str,
    user: DriverUserDep,
    svc: InspectionServiceDep,
    driver_svc: DriverServiceDep,
) -> dict:
    driver = await driver_svc.get_driver_by_user_id(user.id)
    return ok(await svc.get_status(inspection_id, driver.id))
