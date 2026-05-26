from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import structlog
from fastapi.requests import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.exceptions import NotFoundError, ValidationError
from app.common.service import BaseService
from app.common.types import AuditContext
from app.modules.audit.service import AuditService
from app.modules.drivers.models import Driver
from app.modules.vehicle_inspections.enums import ChecklistCategory, InspectionResult, InspectionStatus
from app.modules.vehicle_inspections.models import VehicleInspection
from app.modules.vehicle_inspections.repository import InspectionRepository
from app.modules.vehicle_inspections.v1.schemas import (
    AssignedVehicleResponse,
    ChecklistSectionStatus,
    CreateInspectionRequest,
    InspectionDefectSummary,
    InspectionDriverSummary,
    InspectionResponse,
    InspectionStatusResponse,
    InspectionVehicleSummary,
    ReportInspectionDefectRequest,
)
from app.modules.vehicles.enums import DefectStatus
from app.modules.vehicles.models import Vehicle, VehicleDefect, VehicleDefectImage
from app.storage.upload import generate_image_url, upload_to_r2

logger = structlog.get_logger()

_CHECKLIST_LABELS = {
    "INSIDE_CABIN": "Inside Cabin Check",
    "OUTSIDE_VEHICLE": "Outside Vehicle Check",
    "LOAD_EQUIPMENT": "Load & Equipment Check",
}


class InspectionService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._repo = InspectionRepository(session)
        self._audit = AuditService(session)

    # Vehicle lookup

    async def get_assigned_vehicle(self, driver: Driver) -> AssignedVehicleResponse:
        if driver.vehicle_id is None:
            raise ValidationError("No vehicle is currently assigned to you")
        vehicle = await self._get_vehicle(driver.vehicle_id)
        return self._vehicle_response(vehicle)

    async def lookup_vehicle(self, registration_number: str, driver: Driver) -> AssignedVehicleResponse:
        normalized = registration_number.upper().strip()
        stmt = select(Vehicle).where(Vehicle.registration_number == normalized)
        result = await self._session.execute(stmt)
        vehicle = result.scalar_one_or_none()
        if vehicle is None:
            raise NotFoundError(resource="vehicle", id=registration_number)

        if driver.vehicle_id != vehicle.id:
            raise ValidationError("This vehicle is not assigned to you")

        return self._vehicle_response(vehicle)

    # Create inspection

    async def create_inspection(
        self,
        driver: Driver,
        data: CreateInspectionRequest,
        ctx: AuditContext,
    ) -> VehicleInspection:
        # Verify vehicle + assignment
        normalized = data.registration_number.upper().strip()
        stmt = select(Vehicle).where(Vehicle.registration_number == normalized)
        result = await self._session.execute(stmt)
        vehicle = result.scalar_one_or_none()
        if vehicle is None:
            raise NotFoundError(resource="vehicle", id=data.registration_number)

        if driver.vehicle_id != vehicle.id:
            raise ValidationError("This vehicle is not assigned to you")

        existing_in_progress = await self._repo.find_latest_by_driver_and_vehicle_and_status(
            driver.id,
            vehicle.id,
            InspectionStatus.IN_PROGRESS,
        )
        if existing_in_progress is not None:
            raise ValidationError("An in-progress vehicle inspection already exists for this route and vehicle")

        # Build checklist JSONB
        checklist_data = {}
        for section in data.checklist:
            checklist_data[section.category.value] = [
                {"item": item.item, "checked": item.checked} for item in section.items
            ]

        inspection = await self._repo.create({
            "vehicle_id": vehicle.id,
            "driver_id": driver.id,
            "inspection_type": data.inspection_type,
            "status": InspectionStatus.IN_PROGRESS,
            "mileage": data.mileage,
            "checklist": checklist_data,
            "latitude": data.latitude,
            "longitude": data.longitude,
            "ip_address": ctx.ip_address,
            "notes": data.notes,
        })

        await self._audit.log(
            action="inspection.created",
            entity_type="vehicle_inspection",
            entity_id=inspection.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={"vehicle_id": vehicle.id},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        logger.info("inspection.created", inspection_id=inspection.id, vehicle_id=vehicle.id)

        return await self._load_inspection(inspection.id)

    # Report defect

    async def report_defect(
        self,
        inspection_id: str,
        driver_id: str,
        data: ReportInspectionDefectRequest,
        ctx: AuditContext,
        images: list[tuple[bytes, str, str]] | None = None,
    ) -> VehicleDefect:
        inspection = await self._get_inspection_for_driver(inspection_id, driver_id)
        if inspection.status != InspectionStatus.IN_PROGRESS:
            raise ValidationError("Cannot report defects on a finalized inspection")

        defect = VehicleDefect(
            id=str(uuid4()),
            vehicle_id=inspection.vehicle_id,
            inspection_id=inspection.id,
            reported_by_id=ctx.user_id,
            reported_at=datetime.now(UTC),
            category=data.category,
            severity=data.severity,
            status=DefectStatus.PENDING,
            description=data.description,
            allowed_to_drive=False,
        )
        self._session.add(defect)
        await self._session.flush()

        # Upload images
        if images:
            for content, filename, content_type in images:
                ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
                file_path = await upload_to_r2(
                    f"inspections/{inspection.id}/defects/{defect.id}/{uuid4().hex}.{ext}",
                    content,
                    content_type,
                )
                img = VehicleDefectImage(
                    id=str(uuid4()),
                    defect_id=defect.id,
                    file_path=file_path,
                    uploaded_by_id=ctx.user_id,
                )
                self._session.add(img)
            await self._session.flush()

        await self._audit.log(
            action="inspection.defect_reported",
            entity_type="vehicle_defect",
            entity_id=defect.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={
                "inspection_id": inspection_id,
                "category": data.category,
                "severity": data.severity.value,
            },
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        logger.info("inspection.defect_reported", inspection_id=inspection_id, defect_id=defect.id)

        # Reload with images
        reload_stmt = (
            select(VehicleDefect)
            .where(VehicleDefect.id == defect.id)
            .options(selectinload(VehicleDefect.images))
        )
        reload_result = await self._session.execute(reload_stmt)
        return reload_result.scalar_one()

    # Get inspection summary

    async def get_inspection(self, inspection_id: str) -> VehicleInspection:
        return await self._load_inspection(inspection_id)

    async def delete_inspection(self, inspection_id: str, driver_id: str, ctx: AuditContext) -> None:
        inspection = await self._get_inspection_for_driver(inspection_id, driver_id)
        await self._repo.hard_delete(inspection_id)
        await self._audit.log(
            action="inspection.deleted",
            entity_type="vehicle_inspection",
            entity_id=inspection_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={
                "vehicle_id": inspection.vehicle_id,
                "driver_id": inspection.driver_id,
                "status": inspection.status.value,
            },
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        logger.info("inspection.deleted", inspection_id=inspection_id, driver_id=driver_id)

    # Sign inspection

    async def sign_inspection(
        self,
        inspection_id: str,
        driver_id: str,
        declaration_accepted: bool,
        ctx: AuditContext,
        signature_file: tuple[bytes, str, str] | None = None,
    ) -> VehicleInspection:
        inspection = await self._get_inspection_for_driver(inspection_id, driver_id)
        if inspection.status != InspectionStatus.IN_PROGRESS:
            raise ValidationError("Inspection has already been finalized")

        if not declaration_accepted:
            raise ValidationError("Declaration must be accepted")

        # Upload signature
        signature_path: str | None = None
        if signature_file:
            content, filename, content_type = signature_file
            ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
            signature_path = await upload_to_r2(
                f"inspections/{inspection.id}/signatures/{uuid4().hex}.{ext}",
                content,
                content_type,
            )

        # Check if any defects were reported
        defects = await self._get_linked_defects(inspection_id)
        has_defects = len(defects) > 0
        result = InspectionResult.FAIL if has_defects else InspectionResult.PASS
        new_status = InspectionStatus.AWAITING_RESOLUTION if has_defects else InspectionStatus.COMPLETED

        await self._repo.update_by_id(inspection_id, {
            "result": result,
            "status": new_status,
            "declaration_accepted": True,
            "signature_path": signature_path,
        })

        await self._audit.log(
            action="inspection.signed",
            entity_type="vehicle_inspection",
            entity_id=inspection_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={"result": result.value, "status": new_status.value, "defect_count": len(defects)},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        logger.info("inspection.signed", inspection_id=inspection_id, result=result.value, status=new_status.value)

        return await self._load_inspection(inspection_id)

    # Poll status

    async def get_status(self, inspection_id: str, driver_id: str) -> InspectionStatusResponse:
        inspection = await self._get_inspection_for_driver(inspection_id, driver_id)
        return await self._status_response_for_inspection(inspection)

    async def get_pending_status(self, driver: Driver) -> InspectionStatusResponse:
        if driver.vehicle_id is None:
            raise ValidationError("No vehicle is currently assigned to you")
        vehicle = await self._get_vehicle(driver.vehicle_id)

        inspection = await self._repo.find_latest_by_driver_and_vehicle_and_status(
            driver.id,
            vehicle.id,
            InspectionStatus.IN_PROGRESS,
        )
        if inspection is None:
            raise NotFoundError(resource="vehicle_inspection", id="pending")
        return await self._status_response_for_inspection(inspection)

    async def get_latest_trip_status(self, driver: Driver) -> InspectionStatusResponse | None:
        inspection = await self._repo.find_latest_by_driver_not_in_progress(driver.id)
        if inspection is None:
            return None
        return await self._status_response_for_inspection(inspection)

    # Response builder

    async def to_response(self, inspection: VehicleInspection) -> InspectionResponse:
        vehicle = inspection.vehicle
        driver = inspection.driver
        user = getattr(driver, "user", None)

        # Checklist status
        checklist_status: list[ChecklistSectionStatus] = []
        if inspection.checklist:
            for cat_key, items in inspection.checklist.items():
                all_checked = all(i.get("checked", False) for i in items)
                category = cat_key if isinstance(cat_key, str) else str(cat_key)
                category_enum = ChecklistCategory(category)
                checklist_status.append(ChecklistSectionStatus(
                    category=category_enum,
                    label=_CHECKLIST_LABELS.get(category_enum.value) or category_enum.value,
                    completed=all_checked,
                ))

        # Linked defects
        defects = await self._get_linked_defects(inspection.id)
        defect_summaries: list[InspectionDefectSummary] = []
        for d in defects:
            image_urls = [generate_image_url(img.file_path) for img in (d.images or [])]
            defect_summaries.append(InspectionDefectSummary(
                id=d.id,
                reference=d.reference,
                category=d.category,
                severity=d.severity,
                status=d.status,
                description=d.description,
                allowed_to_drive=d.allowed_to_drive,
                images=image_urls,
            ))

        signature_url = generate_image_url(inspection.signature_path) if inspection.signature_path else None

        return InspectionResponse(
            id=inspection.id,
            vehicle=InspectionVehicleSummary(
                id=vehicle.id,
                registration_number=vehicle.registration_number,
                make=vehicle.make,
                model=vehicle.model,
                fleet_number=vehicle.fleet_number,
            ),
            driver=InspectionDriverSummary(
                id=driver.id,
                first_name=getattr(user, "first_name", "") if user else "",
                last_name=getattr(user, "last_name", "") if user else "",
            ),
            inspection_type=inspection.inspection_type,
            result=inspection.result,
            status=inspection.status,
            mileage=inspection.mileage,
            checklist_status=checklist_status,
            defects=defect_summaries,
            declaration_accepted=inspection.declaration_accepted,
            signature_url=signature_url,
            latitude=inspection.latitude,
            longitude=inspection.longitude,
            ip_address=inspection.ip_address,
            notes=inspection.notes,
            created_at=inspection.created_at,
        )

    # Private helpers

    async def _get_vehicle(self, vehicle_id: str) -> Vehicle:
        stmt = select(Vehicle).where(Vehicle.id == vehicle_id)
        result = await self._session.execute(stmt)
        vehicle = result.scalar_one_or_none()
        if vehicle is None:
            raise NotFoundError(resource="vehicle", id=vehicle_id)
        return vehicle

    async def _load_inspection(self, inspection_id: str) -> VehicleInspection:
        inspection = await self._repo.get_by_id_with_vehicle_and_driver(inspection_id)
        if inspection is None:
            raise NotFoundError(resource="vehicle_inspection", id=inspection_id)
        return inspection

    async def _get_inspection_for_driver(self, inspection_id: str, driver_id: str) -> VehicleInspection:
        inspection = await self._load_inspection(inspection_id)
        if inspection.driver_id != driver_id:
            raise NotFoundError(resource="vehicle_inspection", id=inspection_id)
        return inspection

    async def _get_linked_defects(self, inspection_id: str) -> list[VehicleDefect]:
        stmt = (
            select(VehicleDefect)
            .where(VehicleDefect.inspection_id == inspection_id)
            .options(selectinload(VehicleDefect.images))
            .order_by(VehicleDefect.reported_at)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _status_response_for_inspection(self, inspection: VehicleInspection) -> InspectionStatusResponse:
        defects = await self._get_linked_defects(inspection.id)
        total = len(defects)
        resolved = sum(1 for d in defects if d.status == DefectStatus.RESOLVED)
        allowed = sum(1 for d in defects if d.allowed_to_drive and d.status != DefectStatus.RESOLVED)
        can_proceed = total == 0 or resolved == total or (allowed + resolved) == total
        if can_proceed and inspection.status == InspectionStatus.AWAITING_RESOLUTION:
            await self._repo.update_by_id(inspection.id, {"status": InspectionStatus.RESOLVED})
            logger.info("inspection.auto_resolved", inspection_id=inspection.id)
            return InspectionStatusResponse(
                inspection_id=inspection.id,
                status=InspectionStatus.RESOLVED,
                total_defects=total,
                resolved_defects=resolved,
                allowed_to_drive_count=allowed,
                can_proceed=True,
            )
        return InspectionStatusResponse(
            inspection_id=inspection.id,
            status=inspection.status,
            total_defects=total,
            resolved_defects=resolved,
            allowed_to_drive_count=allowed,
            can_proceed=can_proceed,
        )

    def _vehicle_response(self, vehicle: Vehicle) -> AssignedVehicleResponse:
        return AssignedVehicleResponse(
            id=vehicle.id,
            registration_number=vehicle.registration_number,
            make=vehicle.make,
            model=vehicle.model,
            year=vehicle.year,
            fleet_number=vehicle.fleet_number,
            fleet_custom_name=vehicle.fleet_custom_name,
        )
