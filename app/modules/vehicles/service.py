from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import Any, cast
from uuid import uuid4

import structlog
from fastapi.requests import Request
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.common.exceptions import ConflictError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.common.types import AuditContext
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.drivers.models import Driver
from app.modules.notifications.dispatch import notify
from app.modules.notifications.enums import NotificationEvent, NotificationType
from app.modules.orders.models import DeliveryStop, Order, Package, StopNote
from app.modules.orders.repository import StopNoteRepository
from app.modules.orders.stop_note_utils import batch_package_ids_for_stop_notes
from app.modules.orders.v1.schemas import StopNoteEntry, StopNoteImageEntry
from app.modules.pickup_addresses.models import PickupAddress
from app.modules.planning.enums import RouteStatus, RouteStopFlowType, RouteStopStatus, RouteType
from app.modules.planning.models import Route, RouteEvent, RoutePlan, RouteStop
from app.modules.planning.repository import RouteCalendarRepository
from app.modules.user.models import User
from app.modules.user.repository import UserRepository
from app.modules.vehicles.enums import (
    CardDisplayUnit,
    DefectStatus,
    DocumentType,
    MotFilterStatus,
    ScheduleCalendarFilterKind,
    ScheduleEntrySource,
    ScheduleEventType,
    ServiceBadgeStatus,
    TaxFilterStatus,
    VehicleAvailability,
    VehicleStatus,
    VehicleType,
)
from app.modules.vehicles.models import (
    Vehicle,
    VehicleDefect,
    VehicleDeletionLog,
    VehicleDocument,
    VehicleDraft,
    VehicleImage,
    VehicleMaintenanceRecord,
    VehicleScheduleEntry,
    VehicleServiceRecord,
)
from app.modules.vehicles.repository import (
    VehicleDefectImageRepository,
    VehicleDefectRepository,
    VehicleDeletionRepository,
    VehicleDocumentRepository,
    VehicleDraftRepository,
    VehicleImageRepository,
    VehicleMaintenanceRepository,
    VehicleRepository,
    VehicleScheduleRepository,
    VehicleServiceRepository,
)
from app.modules.vehicles.types import BulkDocumentUploadOutcome, BulkImageUploadOutcome, BulkUploadFailure
from app.modules.vehicles.utils import add_calendar_months
from app.modules.vehicles.v1.schemas import (
    AddServiceRecordRequest,
    ChangeAvailabilityRequest,
    CompliancePercentageBar,
    CreateVehicleData,
    CreateVehicleRequest,
    DefectResponse,
    DefectsSummary,
    DeletedByUser,
    DeletedVehicleListItem,
    DocumentResponse,
    DraftImageItem,
    DraftListItem,
    DraftVehicleData,
    FileUploadFailure,
    InfoCard,
    LogMaintenanceRequest,
    MotComplianceBadge,
    PreferredDriverSummary,
    ReportDefectRequest,
    SaveDraftRequest,
    ScheduleEvent,
    ScheduleEventDetails,
    ScheduleResponse,
    ServiceBadge,
    TaxComplianceBadge,
    UpdateDefectRequest,
    UpdateDocumentMetadataRequest,
    UpdateDraftRequest,
    UpdateMaintenanceRecordRequest,
    UpdateMileageRequest,
    UpdateServiceRecordRequest,
    UpdateVehicleSpecsRequest,
    UploadDocumentRequest,
    UserSchema,
    UtilizationSummary,
    VehicleListItem,
    VehicleResponse,
)
from app.storage.upload import (
    BulkUploadResult,
    bulk_upload_images,
    bulk_upload_to_r2,
    delete_from_r2,
    delete_image,
    generate_document_url,
    generate_image_url,
    upload_to_r2,
)

logger = structlog.get_logger()

_VEHICLE_RESPONSE_SKIP_FIELDS = frozenset(
    {
        "images",
        "preferred_driver",
        "next_service_card",
        "current_mileage_card",
        "efficiency_card",
    }
)

_KM_TO_MI = 0.6213711922373343


def _route_distance_km_to_miles(km: float | None) -> float | None:
    if km is None:
        return None
    return round(float(km) * _KM_TO_MI, 1)


class VehicleService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._vehicle_repo = VehicleRepository(session)
        self._draft_repo = VehicleDraftRepository(session)
        self._maintenance_repo: VehicleMaintenanceRepository = VehicleMaintenanceRepository(session)
        self._defect_repo = VehicleDefectRepository(session)
        self._service_repo = VehicleServiceRepository(session)
        self._document_repo = VehicleDocumentRepository(session)
        self._image_repo = VehicleImageRepository(session)
        self._defect_image_repo = VehicleDefectImageRepository(session)
        self._schedule_repo = VehicleScheduleRepository(session)
        self._route_calendar_repo = RouteCalendarRepository(session)
        self._vehicle_deletion_repo = VehicleDeletionRepository(session)
        self._user_repo = UserRepository(session)
        self._stop_note_repo = StopNoteRepository(session)
        self._audit = AuditService(session)
        self._ip = request.client.host if request and request.client else None
        self._ua = request.headers.get("user-agent") if request else None

    async def _log_audit(
        self,
        action: str,
        *,
        entity_type: str = "vehicle",
        entity_id: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        reason: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        severity: str = "INFO",
        category: AuditCategory = AuditCategory.FLEET,
        event_type: AuditEventType | str = AuditEventType.VEHICLE_UPDATED,
    ) -> None:
        """Centralised helper for vehicle-related audit logs."""
        await self._audit.log(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user_id,
            user_role=user_role,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            ip_address=ip_address or self._ip,
            user_agent=user_agent or self._ua,
            severity=severity,
            category=category,
            event_type=event_type,
        )

    # Vehicle CRUD

    async def create_vehicle(self, data: CreateVehicleRequest, ctx: AuditContext) -> Vehicle:
        existing = await self._vehicle_repo.find_by_registration(data.registration_number)
        if existing:
            raise ConflictError(f"Vehicle with registration '{data.registration_number}' already exists")

        vehicle_data = data.model_dump(exclude_unset=True, exclude={"initial_maintenance"})
        vehicle_data["status"] = VehicleStatus.ACTIVE
        months = vehicle_data.get("service_interval_months")
        if isinstance(months, int) and months > 0:
            vehicle_data["next_service_due"] = add_calendar_months(date.today(), months)
        cm = vehicle_data.get("current_mileage")
        vehicle_data["last_service_mileage"] = int(cm) if isinstance(cm, int) else None
        if data.availability == VehicleAvailability.IN_MAINTENANCE and data.initial_maintenance is not None:
            vehicle_data["availability_effective_from"] = data.initial_maintenance.date_from
            if data.initial_maintenance.date_to is not None:
                vehicle_data["availability_effective_to"] = data.initial_maintenance.date_to
        else:
            vehicle_data["availability_effective_from"] = date.today()
        vehicle = await self._vehicle_repo.create(vehicle_data)

        await self._audit.log(
            action="vehicle.created",
            entity_type="vehicle",
            entity_id=vehicle.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={"registration_number": vehicle.registration_number, "fleet_number": vehicle.fleet_number},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VEHICLE_CREATED,
        )
        logger.info("vehicle.created", vehicle_id=vehicle.id, fleet_number=vehicle.fleet_number)

        if data.availability == VehicleAvailability.UNAVAILABLE:
            eff_from = vehicle.availability_effective_from or date.today()
            await self._schedule_repo.create(
                {
                    "vehicle_id": vehicle.id,
                    "date_from": eff_from,
                    "date_to": vehicle.availability_effective_to,
                    "type": ScheduleEventType.UNAVAILABLE,
                    "source": ScheduleEntrySource.AVAILABILITY,
                    "source_id": None,
                    "details": None,
                }
            )

        if data.availability == VehicleAvailability.IN_MAINTENANCE and data.initial_maintenance is not None:
            await self._persist_initial_maintenance(vehicle.id, data.initial_maintenance, ctx)

        return vehicle

    async def _delete_draft_preview_maintenance(self, vehicle_id: str) -> None:
        rec = await self._maintenance_repo.find_latest_by_vehicle_id(vehicle_id)
        if rec is None:
            return
        await self._session.execute(
            delete(VehicleScheduleEntry).where(
                VehicleScheduleEntry.vehicle_id == vehicle_id,
                VehicleScheduleEntry.source == ScheduleEntrySource.MAINTENANCE,
                VehicleScheduleEntry.source_id == rec.id,
            )
        )
        await self._maintenance_repo.hard_delete(rec.id)
        logger.info("vehicle.draft_preview_maintenance_removed", vehicle_id=vehicle_id, record_id=rec.id)

    async def _upsert_draft_maintenance_record_only(
        self,
        vehicle_id: str,
        maint: LogMaintenanceRequest,
        ctx: AuditContext,
    ) -> VehicleMaintenanceRecord:
        record_data = maint.model_dump()
        record_data["vehicle_id"] = vehicle_id
        record_data["recorded_by_id"] = ctx.user_id
        record_data["maintenance_types"] = list(maint.maintenance_types)
        existing = await self._maintenance_repo.find_latest_by_vehicle_id(vehicle_id)
        if existing is not None:
            updates = {k: v for k, v in record_data.items() if k != "vehicle_id"}
            return await self._maintenance_repo.update_by_id(existing.id, updates)
        created = await self._maintenance_repo.create(record_data)
        logger.info("vehicle.draft_preview_maintenance_saved", vehicle_id=vehicle_id, record_id=created.id)
        return created

    async def get_vehicle(self, vehicle_id: str) -> Vehicle:
        vehicle = await self._vehicle_repo.get_by_id(vehicle_id)
        if vehicle is None:
            raise NotFoundError(resource="vehicle", id=vehicle_id)
        return vehicle

    async def get_vehicle_with_image_urls(self, vehicle_id: str) -> tuple[Vehicle, list[DraftImageItem]]:
        vehicle = await self._vehicle_repo.get_by_id_with_images(vehicle_id)
        if vehicle is None:
            raise NotFoundError(resource="vehicle", id=vehicle_id)
        items = [DraftImageItem(id=img.id, url=generate_image_url(img.file_path)) for img in vehicle.images]
        return vehicle, items

    async def to_vehicle_response(self, vehicle: object, images: list[DraftImageItem] | None = None) -> VehicleResponse:
        data = {f: getattr(vehicle, f) for f in VehicleResponse.model_fields if f not in _VEHICLE_RESPONSE_SKIP_FIELDS and hasattr(vehicle, f)}
        data["images"] = images if images is not None else []
        data["next_service_card"] = self._next_service_card(vehicle)
        data["current_mileage_card"] = self._current_mileage_card(vehicle)
        data["efficiency_card"] = self._efficiency_card(vehicle)
        data["preferred_driver"] = await self._preferred_driver_summary(vehicle)
        return VehicleResponse(**data)

    async def _preferred_driver_summary(self, vehicle: object) -> PreferredDriverSummary | None:
        preferred_driver_id = getattr(vehicle, "preferred_driver_id", None)
        if not preferred_driver_id:
            return None

        # If relation is already loaded (detail path), reuse it.
        try:
            state = sa_inspect(vehicle)
            unloaded = getattr(state, "unloaded", set())
            if "preferred_driver" not in unloaded:
                driver = getattr(vehicle, "preferred_driver", None)
                if driver is not None:
                    return PreferredDriverSummary(id=driver.id, first_name=driver.first_name, last_name=driver.last_name)
        except Exception:
            pass

        from app.modules.user.models import User

        driver = await self._session.get(User, preferred_driver_id)
        if driver is None:
            return None
        return PreferredDriverSummary(id=driver.id, first_name=driver.first_name, last_name=driver.last_name)

    def _compute_service_remaining(
        self,
        *,
        next_service_due: date | None,
        current_mileage: int | None,
        service_interval_miles: int | None,
        last_service_mileage: int | None,
    ) -> tuple[int | None, int | None]:
        miles_remaining: int | None = None
        if (
            service_interval_miles is not None
            and service_interval_miles > 0
            and current_mileage is not None
            and last_service_mileage is not None
        ):
            miles_remaining = (last_service_mileage + service_interval_miles) - current_mileage

        days_remaining: int | None = None
        if next_service_due is not None:
            days_remaining = (next_service_due - date.today()).days

        return miles_remaining, days_remaining

    def _pick_next_service_display(
        self,
        remaining_miles: int | None,
        remaining_days: int | None,
    ) -> tuple[CardDisplayUnit, int | None, int | None]:
        """Same miles-vs-days rule as next_service_card: ~50 mi ≈ 1 day when both exist.

        Returns (display_unit, display_value, status_basis). status_basis is the raw
        remaining on the chosen axis (for OVERDUE/DUE_SOON); None if unknown.
        """
        if remaining_miles is not None and remaining_days is not None:
            by_miles_days = 0 if remaining_miles == 0 else max(1, remaining_miles // 50)
            if remaining_days <= by_miles_days:
                return CardDisplayUnit.DAYS, max(remaining_days, 0), remaining_days
            return CardDisplayUnit.MILES, max(remaining_miles, 0), remaining_miles
        if remaining_miles is not None:
            return CardDisplayUnit.MILES, max(remaining_miles, 0), remaining_miles
        if remaining_days is not None:
            return CardDisplayUnit.DAYS, max(remaining_days, 0), remaining_days
        return CardDisplayUnit.UNKNOWN, None, None

    def _service_interval_total_days(self, vehicle: Vehicle) -> int | None:
        if vehicle.next_service_due is None:
            return None
        months = vehicle.service_interval_months
        if not isinstance(months, int) or months <= 0:
            return None
        anchor = add_calendar_months(vehicle.next_service_due, -months)
        return max((vehicle.next_service_due - anchor).days, 1)

    def _next_service_card(self, vehicle: object) -> InfoCard:
        current_mileage = getattr(vehicle, "current_mileage", None)
        service_interval_miles = getattr(vehicle, "service_interval_miles", None)
        next_service_due = getattr(vehicle, "next_service_due", None)
        last_service_mileage = getattr(vehicle, "last_service_mileage", None)

        remaining_miles, remaining_days = self._compute_service_remaining(
            next_service_due=next_service_due,
            current_mileage=current_mileage if isinstance(current_mileage, int) else None,
            service_interval_miles=service_interval_miles if isinstance(service_interval_miles, int) else None,
            last_service_mileage=last_service_mileage if isinstance(last_service_mileage, int) else None,
        )

        unit, disp, _basis = self._pick_next_service_display(remaining_miles, remaining_days)
        return InfoCard(display_unit=unit, display_value=disp)

    def _current_mileage_card(self, vehicle: object) -> InfoCard | None:
        current_mileage = getattr(vehicle, "current_mileage", None)
        if not isinstance(current_mileage, int):
            return None

        return InfoCard(display_unit=CardDisplayUnit.MILES, display_value=current_mileage)

    def _efficiency_card(self, vehicle: object) -> InfoCard | None:
        fuel_type = getattr(vehicle, "fuel_type", None)
        range_miles = getattr(vehicle, "range_miles", None)
        average_mpg = getattr(vehicle, "average_mpg", None)

        if fuel_type == "ELECTRIC":
            if isinstance(range_miles, (int, float)) and range_miles > 0:
                return InfoCard(display_unit=CardDisplayUnit.MILES, display_value=int(round(range_miles)))
            return None

        if isinstance(average_mpg, (int, float)) and average_mpg > 0:
            return InfoCard(display_unit=CardDisplayUnit.MPG, display_value=int(round(average_mpg)))
        return None

    def _tax_badge(self, due_date: date | None) -> TaxComplianceBadge:
        if due_date is None:
            return TaxComplianceBadge(status=TaxFilterStatus.MISSING, remaining_days=None, due_date=None)

        remaining_days = (due_date - date.today()).days
        if remaining_days < 0:
            status = TaxFilterStatus.OVERDUE
        elif remaining_days <= 30:
            status = TaxFilterStatus.DUE_SOON
        else:
            status = TaxFilterStatus.PAID
        return TaxComplianceBadge(status=status, remaining_days=remaining_days, due_date=due_date)

    def _mot_badge(self, due_date: date | None) -> MotComplianceBadge:
        if due_date is None:
            return MotComplianceBadge(status=MotFilterStatus.MISSING, remaining_days=None, due_date=None)

        remaining_days = (due_date - date.today()).days
        if remaining_days < 0:
            status = MotFilterStatus.EXPIRED
        elif remaining_days <= 30:
            status = MotFilterStatus.EXPIRING_SOON
        else:
            status = MotFilterStatus.VALID
        return MotComplianceBadge(status=status, remaining_days=remaining_days, due_date=due_date)

    def _service_badge(
        self,
        next_service_due: date | None,
        current_mileage: int | None,
        service_interval_miles: int | None,
        last_service_mileage: int | None,
    ) -> ServiceBadge:
        miles_remaining, days_remaining = self._compute_service_remaining(
            next_service_due=next_service_due,
            current_mileage=current_mileage,
            service_interval_miles=service_interval_miles,
            last_service_mileage=last_service_mileage,
        )

        unit, disp, basis = self._pick_next_service_display(miles_remaining, days_remaining)
        if basis is None:
            return ServiceBadge(status=ServiceBadgeStatus.UNKNOWN, display_unit=CardDisplayUnit.UNKNOWN, display_value=None)
        if basis <= 0:
            status = ServiceBadgeStatus.OVERDUE
        elif basis <= 30:
            status = ServiceBadgeStatus.DUE_SOON
        else:
            status = ServiceBadgeStatus.VALID
        return ServiceBadge(status=status, display_unit=unit, display_value=disp)

    def document_to_response(self, document: VehicleDocument) -> DocumentResponse:
        resp = DocumentResponse.model_validate(document)
        if document.file_path:
            resp.url = generate_document_url(document.file_path)
        return resp

    def defect_to_response(self, defect: VehicleDefect) -> DefectResponse:
        image_urls = [generate_image_url(img.file_path) for img in (defect.images or [])]
        fields = {f for f in DefectResponse.model_fields if f not in {"images", "reported_by"}}
        data = {f: getattr(defect, f) for f in fields if hasattr(defect, f)}
        data["images"] = image_urls
        reporter = getattr(defect, "reported_by", None)
        if reporter is not None:
            data["reported_by"] = UserSchema(
                id=reporter.id,
                first_name=reporter.first_name,
                last_name=reporter.last_name,
            )
        else:
            data["reported_by"] = None
        return DefectResponse.model_validate(data)

    def to_vehicle_list_item(self, vehicle: Vehicle, defect_counts: dict[str, int]) -> VehicleListItem:
        image_urls = [generate_image_url(img.file_path) for img in vehicle.images]
        return VehicleListItem.model_validate(
            {
                "id": vehicle.id,
                "fleet_number": vehicle.fleet_number,
                "registration_number": vehicle.registration_number or "",
                "make": vehicle.make,
                "model": vehicle.model,
                "year": vehicle.year,
                "live_status": vehicle.live_status,
                "availability": vehicle.availability,
                "tax": self._tax_badge(vehicle.tax_due_date),
                "mot": self._mot_badge(vehicle.mot_expiry),
                "service": self._service_badge(
                    vehicle.next_service_due,
                    vehicle.current_mileage if isinstance(vehicle.current_mileage, int) else None,
                    vehicle.service_interval_miles if isinstance(vehicle.service_interval_miles, int) else None,
                    vehicle.last_service_mileage if isinstance(vehicle.last_service_mileage, int) else None,
                ),
                "defects": DefectsSummary(
                    total=defect_counts.get("total", 0),
                    pending=defect_counts.get("pending", 0),
                    in_progress=defect_counts.get("in_progress", 0),
                ),
                "images": image_urls,
            }
        )

    async def list_vehicles(
        self,
        *,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        live_status: list[str] | None = None,
        availability: list[str] | None = None,
        mot_status: list[MotFilterStatus] | None = None,
        tax_status: list[TaxFilterStatus] | None = None,
    ) -> tuple[list[Vehicle], int, dict[str, dict[str, int]]]:
        rows, total = await self._vehicle_repo.search_with_defect_counts(
            page=page,
            size=size,
            search=search,
            live_status=live_status,
            availability=availability,
            mot_status=mot_status,
            tax_status=tax_status,
        )
        vehicles = [r[0] for r in rows]
        defect_counts = {r[0].id: {"total": r[1], "pending": r[2], "in_progress": r[3]} for r in rows}
        return vehicles, total, defect_counts

    async def get_fleet_stats(self) -> dict[str, int]:
        return await self._vehicle_repo.get_fleet_stats()

    async def update_specs(self, vehicle_id: str, data: UpdateVehicleSpecsRequest, ctx: AuditContext) -> Vehicle:
        old_vehicle = await self.get_vehicle(vehicle_id)
        update_data = data.model_dump(exclude_unset=True)

        new_months = update_data.get("service_interval_months")
        old_months = old_vehicle.service_interval_months
        if isinstance(new_months, int) and new_months != old_months:
            update_data["next_service_due"] = self._next_service_due_after_month_interval_change(
                previous_due=old_vehicle.next_service_due,
                previous_months=old_months if isinstance(old_months, int) else None,
                new_months=new_months,
            )

        old_snapshot = {k: getattr(old_vehicle, k, None) for k in update_data}

        vehicle = await self._vehicle_repo.update_by_id(vehicle_id, update_data)

        await self._log_audit(
            action="vehicle.specs_updated",
            entity_type="vehicle",
            entity_id=vehicle_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value=old_snapshot,
            new_value=update_data,
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VEHICLE_UPDATED,
        )
        logger.info("vehicle.specs_updated", vehicle_id=vehicle_id)
        return vehicle

    async def update_mileage(self, vehicle_id: str, data: UpdateMileageRequest, ctx: AuditContext) -> Vehicle:
        vehicle = await self.get_vehicle(vehicle_id)
        if vehicle.current_mileage is not None and data.new_mileage < vehicle.current_mileage:
            raise ValidationError(f"New mileage ({data.new_mileage}) cannot be less than current mileage ({vehicle.current_mileage})")

        old_mileage = vehicle.current_mileage
        updated = await self._vehicle_repo.update_by_id(vehicle_id, {"current_mileage": data.new_mileage})

        await self._log_audit(
            action="vehicle.mileage_updated",
            entity_type="vehicle",
            entity_id=vehicle_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"current_mileage": old_mileage},
            new_value={"current_mileage": data.new_mileage},
            severity="INFO",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.MILEAGE_UPDATED,
        )
        logger.info("vehicle.mileage_updated", vehicle_id=vehicle_id, old=old_mileage, new=data.new_mileage)
        await self.evaluate_driver_service_due_alerts_for_vehicle(updated, today=date.today())
        return updated

    async def change_availability(self, vehicle_id: str, data: ChangeAvailabilityRequest, ctx: AuditContext) -> Vehicle:
        vehicle = await self.get_vehicle(vehicle_id)
        old_availability = vehicle.availability

        if old_availability == data.availability:
            raise ValidationError(f"Vehicle is already {data.availability}")

        effective_from_date = data.effective_from

        if data.availability == VehicleAvailability.UNAVAILABLE and effective_from_date is not None:
            close_date = effective_from_date - timedelta(days=1)
            if close_date >= date.today():
                close_date = date.today()
            await self._schedule_repo.close_availability_ranges(vehicle_id, close_date)
            await self._schedule_repo.create(
                {
                    "vehicle_id": vehicle_id,
                    "date_from": effective_from_date,
                    "date_to": data.effective_to,
                    "type": ScheduleEventType.UNAVAILABLE,
                    "source": ScheduleEntrySource.AVAILABILITY,
                    "source_id": None,
                    "details": None,
                }
            )
        elif old_availability == VehicleAvailability.UNAVAILABLE:
            await self._schedule_repo.close_availability_ranges(vehicle_id, date.today())

        update_data: dict[str, Any] = {
            "availability": data.availability,
            "availability_effective_from": data.effective_from,
            "availability_effective_to": data.effective_to,
        }
        updated = await self._vehicle_repo.update_by_id(vehicle_id, update_data)

        new_value_serializable: dict[str, Any] = {
            "availability": data.availability,
            "availability_effective_from": data.effective_from.isoformat() if data.effective_from else None,
            "availability_effective_to": data.effective_to.isoformat() if data.effective_to else None,
        }
        await self._log_audit(
            action="vehicle.availability_changed",
            entity_type="vehicle",
            entity_id=vehicle_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"availability": old_availability},
            new_value=new_value_serializable,
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VEHICLE_UPDATED,
        )
        logger.info("vehicle.availability_changed", vehicle_id=vehicle_id, old=old_availability, new=data.availability)
        return updated

    async def _cleanup_vehicle_files(self, vehicle_id: str) -> None:
        """Delete all images and documents from R2 for a vehicle."""
        images = await self._image_repo.find_by_vehicle(vehicle_id)
        for img in images:
            if img.file_path:
                try:
                    await delete_image(img.file_path)
                except Exception:
                    logger.warning("vehicle.image_r2_cleanup_failed", vehicle_id=vehicle_id, file_path=img.file_path)

        docs = await self._document_repo.find_by_vehicle(vehicle_id)
        for doc in docs:
            if doc.file_path:
                try:
                    await delete_from_r2(doc.file_path)
                except Exception:
                    logger.warning("vehicle.document_r2_cleanup_failed", vehicle_id=vehicle_id, file_path=doc.file_path)

    async def delete_vehicle(self, vehicle_id: str, reason: str, ctx: AuditContext) -> None:
        vehicle = await self._vehicle_repo.get_by_id(vehicle_id)
        if vehicle is None:
            raise NotFoundError(resource="vehicle", id=vehicle_id)

        vtype = vehicle.vehicle_type.value if vehicle.vehicle_type is not None else None
        deleted_ts = datetime.now(UTC)

        await self._vehicle_deletion_repo.create(
            {
                "vehicle_id": vehicle.id,
                "registration_number": vehicle.registration_number,
                "make": vehicle.make,
                "model": vehicle.model,
                "vehicle_type": vtype,
                "deletion_reason": reason,
                "deleted_by_id": ctx.user_id,
            }
        )

        await self._cleanup_vehicle_files(vehicle_id)
        await self._vehicle_repo.hard_delete(vehicle_id)

        await self._log_audit(
            action="vehicle.deleted",
            entity_type="vehicle",
            entity_id=vehicle_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={
                "registration_number": vehicle.registration_number,
                "status": vehicle.status.value if hasattr(vehicle.status, "value") else str(vehicle.status),
            },
            new_value={"reason": reason, "created_at": deleted_ts.isoformat()},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            severity="CRITICAL",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VEHICLE_DECOMMISSIONED,
        )
        logger.info("vehicle.deleted", vehicle_id=vehicle_id)

    async def list_deleted_vehicles(
        self,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[VehicleDeletionLog], int]:
        return await self._vehicle_deletion_repo.list_paginated(page=page, size=size)

    def deleted_vehicle_to_list_item(self, row: VehicleDeletionLog) -> DeletedVehicleListItem:
        vt: VehicleType | None = None
        if row.vehicle_type:
            try:
                vt = VehicleType(row.vehicle_type)
            except ValueError:
                vt = None
        by: DeletedByUser | None = None
        if row.deleted_by is not None:
            by = DeletedByUser(
                first_name=row.deleted_by.first_name,
                last_name=row.deleted_by.last_name,
                email=row.deleted_by.email,
            )
        return DeletedVehicleListItem(
            id=row.vehicle_id,
            registration_number=row.registration_number,
            make=row.make,
            model=row.model,
            vehicle_type=vt,
            deletion_reason=row.deletion_reason,
            created_at=row.created_at,
            deleted_by=by,
        )

    # Drafts

    async def save_draft(self, data: SaveDraftRequest, ctx: AuditContext) -> tuple[VehicleDraft, Vehicle]:
        effective_availability = data.availability or VehicleAvailability.ACTIVE
        vehicle_data = data.model_dump(exclude_unset=True, exclude={"initial_maintenance"})
        vehicle_data["status"] = VehicleStatus.DRAFT
        vehicle_data["availability"] = effective_availability
        if effective_availability == VehicleAvailability.IN_MAINTENANCE and data.initial_maintenance is not None:
            vehicle_data["availability_effective_from"] = data.initial_maintenance.date_from
            if data.initial_maintenance.date_to is not None:
                vehicle_data["availability_effective_to"] = data.initial_maintenance.date_to
        else:
            vehicle_data.setdefault("availability_effective_from", date.today())

        if data.registration_number:
            existing = await self._vehicle_repo.find_by_registration(data.registration_number)
            if existing:
                raise ConflictError(f"Vehicle with registration '{data.registration_number}' already exists")

        vehicle = await self._vehicle_repo.create(vehicle_data)
        draft = await self._draft_repo.create({"vehicle_id": vehicle.id, "created_by_id": ctx.user_id})

        if effective_availability == VehicleAvailability.IN_MAINTENANCE and data.initial_maintenance is not None:
            await self._upsert_draft_maintenance_record_only(vehicle.id, data.initial_maintenance, ctx)

        await self._audit.log(
            action="vehicle.draft_saved",
            entity_type="vehicle_draft",
            entity_id=draft.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={"draft_number": draft.draft_number, "vehicle_id": vehicle.id},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VEHICLE_UPDATED,
        )
        logger.info("vehicle.draft_saved", draft_id=draft.id, draft_number=draft.draft_number, vehicle_id=vehicle.id)
        return draft, vehicle

    async def update_draft(self, draft_id: str, data: UpdateDraftRequest, ctx: AuditContext) -> tuple[VehicleDraft, Vehicle]:
        draft = await self._draft_repo.get_by_id(draft_id)
        if draft is None:
            raise NotFoundError(resource="vehicle_draft", id=draft_id)

        vehicle = await self.get_vehicle(draft.vehicle_id)
        if vehicle.status != VehicleStatus.DRAFT:
            raise ValidationError("Cannot update a draft that has already been published")

        incoming = data.model_dump(exclude_unset=True)
        maint_raw = incoming.pop("initial_maintenance", None)
        maint_model: LogMaintenanceRequest | None = LogMaintenanceRequest.model_validate(maint_raw) if maint_raw is not None else None
        update_data: dict[str, Any] = dict(incoming)

        eff_avail = data.availability if data.availability is not None else vehicle.availability
        if maint_model is not None and eff_avail != VehicleAvailability.IN_MAINTENANCE:
            raise ValidationError("initial_maintenance is only allowed when availability is IN_MAINTENANCE")
        if eff_avail == VehicleAvailability.IN_MAINTENANCE and maint_model is None:
            raise ValidationError("initial_maintenance is required when availability is IN_MAINTENANCE")

        if eff_avail == VehicleAvailability.IN_MAINTENANCE and maint_model is not None:
            update_data["availability_effective_from"] = maint_model.date_from
            update_data["availability_effective_to"] = maint_model.date_to
        elif data.availability is not None and data.availability != VehicleAvailability.IN_MAINTENANCE:
            update_data.setdefault("availability_effective_from", date.today())
            update_data["availability_effective_to"] = None

        if update_data:
            if data.registration_number and data.registration_number != vehicle.registration_number:
                existing = await self._vehicle_repo.find_by_registration(data.registration_number)
                if existing:
                    raise ConflictError(f"Vehicle with registration '{data.registration_number}' already exists")

            vehicle = await self._vehicle_repo.update_by_id(draft.vehicle_id, update_data)

            await self._audit.log(
                action="vehicle.draft_updated",
                entity_type="vehicle_draft",
                entity_id=draft.id,
                user_id=ctx.user_id,
                user_role=ctx.user_role,
                new_value=update_data,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                severity="NOTICE",
                category=AuditCategory.FLEET,
                event_type=AuditEventType.VEHICLE_UPDATED,
            )
            logger.info("vehicle.draft_updated", draft_id=draft.id, vehicle_id=draft.vehicle_id)

        final_vehicle = await self.get_vehicle(draft.vehicle_id)
        if final_vehicle.availability != VehicleAvailability.IN_MAINTENANCE:
            await self._delete_draft_preview_maintenance(draft.vehicle_id)
        if final_vehicle.availability == VehicleAvailability.IN_MAINTENANCE and maint_model is not None:
            await self._upsert_draft_maintenance_record_only(draft.vehicle_id, maint_model, ctx)

        return draft, final_vehicle

    async def get_draft(self, draft_id: str) -> tuple[VehicleDraft, Vehicle]:
        draft = await self._draft_repo.get_by_id_with_vehicle_if_draft(draft_id)
        if draft is None:
            raise NotFoundError(resource="vehicle_draft", id=draft_id)
        return draft, draft.vehicle

    async def list_drafts(
        self,
        *,
        page: int = 1,
        size: int = 20,
        order_desc: bool = True,
        search: str | None = None,
    ) -> tuple[list[DraftListItem], int]:
        drafts, total = await self._draft_repo.list_drafts(page=page, size=size, order_desc=order_desc, search=search)
        items = [self.draft_to_list_item(d, d.vehicle) for d in drafts]
        return items, total

    async def delete_draft(self, draft_id: str, ctx: AuditContext) -> None:
        draft = await self._draft_repo.get_by_id(draft_id)
        if draft is None:
            raise NotFoundError(resource="vehicle_draft", id=draft_id)

        vehicle = await self._vehicle_repo.get_by_id(draft.vehicle_id)
        if vehicle is not None and vehicle.status != VehicleStatus.DRAFT:
            raise ValidationError("Cannot delete a draft that has already been published")

        if vehicle is not None:
            await self._cleanup_vehicle_files(vehicle.id)

        await self._draft_repo.hard_delete(draft_id)
        if vehicle is not None:
            await self._vehicle_repo.hard_delete(draft.vehicle_id)

        await self._audit.log(
            action="vehicle.draft_deleted",
            entity_type="vehicle_draft",
            entity_id=draft_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"draft_number": draft.draft_number, "vehicle_id": draft.vehicle_id},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            severity="CRITICAL",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VEHICLE_UPDATED,
        )
        logger.info("vehicle.draft_deleted", draft_id=draft_id, vehicle_id=draft.vehicle_id)

    def _maintenance_record_to_log_request(self, record: VehicleMaintenanceRecord) -> LogMaintenanceRequest:
        raw_types = record.maintenance_types or []
        types = [str(t) for t in raw_types]
        return LogMaintenanceRequest(
            maintenance_types=types,
            provider_type=record.provider_type,
            date_from=record.date_from,
            cost=float(record.cost or 0),
            date_to=record.date_to,
            notes=record.notes,
            garage=record.garage,
        )

    async def _persist_initial_maintenance(
        self,
        vehicle_id: str,
        maint: LogMaintenanceRequest,
        ctx: AuditContext,
    ) -> VehicleMaintenanceRecord:
        record_data = maint.model_dump()
        record_data["vehicle_id"] = vehicle_id
        record_data["recorded_by_id"] = ctx.user_id
        record_data["maintenance_types"] = list(maint.maintenance_types)
        record = await self._maintenance_repo.create(record_data)
        await self._replace_maintenance_schedule_entry(vehicle_id, record)
        await self._log_audit(
            action="vehicle.maintenance.initial_log",
            entity_type="vehicle_maintenance",
            entity_id=record.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={"vehicle_id": vehicle_id, "types": record_data["maintenance_types"]},
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.MAINTENANCE_LOGGED,
        )
        logger.info("vehicle.maintenance_logged", vehicle_id=vehicle_id, record_id=record.id, on_create=True)
        return record

    async def update_draft_document_metadata(
        self,
        vehicle_id: str,
        updates: list[UpdateDocumentMetadataRequest],
        ctx: AuditContext,
    ) -> None:
        for item in updates:
            doc = await self._document_repo.get_by_id(item.id)
            if doc is None or doc.vehicle_id != vehicle_id:
                raise NotFoundError(resource="document", id=item.id)

            update_data = item.model_dump(exclude={"id"}, exclude_unset=True)
            if update_data:
                await self._document_repo.update_by_id(item.id, update_data)

    async def handle_draft_image_uploads(
        self,
        vehicle_id: str,
        validated_images: list[tuple[bytes, str, str]],
        ctx: AuditContext,
    ) -> list[FileUploadFailure]:
        if not validated_images:
            return []

        outcome = await self.add_images(vehicle_id, validated_images, ctx)
        failures: list[FileUploadFailure] = []
        filename_by_idx = [v[1] for v in validated_images]
        for f in outcome.failed:
            filename = filename_by_idx[f.index] if f.index < len(filename_by_idx) else "image"
            reason = f.message if f.message != "File upload failed" else "File upload failed, please retry this image"
            failures.append(FileUploadFailure(index=f.index, filename=filename, reason=reason))
        return failures

    async def handle_draft_document_uploads(
        self,
        vehicle_id: str,
        validated_documents: list[tuple[bytes, str, str]],
        documents_metadata: list | None,
        ctx: AuditContext,
    ) -> list[FileUploadFailure]:
        if not validated_documents or not documents_metadata:
            return []

        docs_to_upload = [(idx, documents_metadata[idx], validated) for idx, validated in enumerate(validated_documents)]
        filename_by_idx = [v[1] for v in validated_documents]

        outcome = await self.add_documents_bulk(vehicle_id, docs_to_upload, ctx)
        failures: list[FileUploadFailure] = []
        for f in outcome.failed:
            filename = filename_by_idx[f.index] if f.index < len(filename_by_idx) else "document"
            reason = f.message if f.message != "File upload failed" else "File upload failed, please retry this document"
            failures.append(FileUploadFailure(index=f.index, filename=filename, reason=reason))
        return failures

    async def delete_draft_images(self, vehicle_id: str, image_ids: list[str], ctx: AuditContext) -> None:
        for image_id in image_ids:
            try:
                await self.delete_image(vehicle_id, image_id, ctx)
            except NotFoundError:
                logger.warning("vehicle.draft_image_not_found", vehicle_id=vehicle_id, image_id=image_id)

    async def delete_draft_documents(self, vehicle_id: str, document_ids: list[str], ctx: AuditContext) -> None:
        for document_id in document_ids:
            try:
                await self.delete_document(vehicle_id, document_id, ctx)
            except NotFoundError:
                logger.warning("vehicle.draft_document_not_found", vehicle_id=vehicle_id, document_id=document_id)

    async def get_draft_all_images(self, vehicle_id: str) -> list[DraftImageItem]:
        images = await self._image_repo.find_by_vehicle(vehicle_id)
        return [DraftImageItem(id=img.id, url=generate_image_url(img.file_path)) for img in images]

    async def get_draft_all_documents(self, vehicle_id: str) -> list[DocumentResponse]:
        docs = await self._document_repo.find_by_vehicle(vehicle_id)
        return [self.document_to_response(d) for d in docs]

    async def draft_to_response(
        self,
        draft: VehicleDraft,
        vehicle: Vehicle,
        images: list[DraftImageItem] | None = None,
        documents: list[DocumentResponse] | None = None,
    ) -> DraftVehicleData:
        rec = await self._maintenance_repo.find_latest_by_vehicle_id(vehicle.id)
        initial = self._maintenance_record_to_log_request(rec) if rec is not None else None
        return DraftVehicleData(
            id=draft.id,
            draft_number=draft.draft_number,
            vehicle_id=vehicle.id,
            registration_number=vehicle.registration_number,
            fleet_number=vehicle.fleet_number,
            fleet_custom_name=vehicle.fleet_custom_name,
            make=vehicle.make,
            model=vehicle.model,
            year=vehicle.year,
            vehicle_type=vehicle.vehicle_type,
            fuel_type=vehicle.fuel_type,
            cargo_volume_m3=vehicle.cargo_volume_m3,
            max_payload_kg=vehicle.max_payload_kg,
            average_mpg=vehicle.average_mpg,
            range_miles=vehicle.range_miles,
            current_mileage=vehicle.current_mileage,
            service_interval_miles=vehicle.service_interval_miles,
            service_interval_months=vehicle.service_interval_months,
            max_continuous_driving_hours=vehicle.max_continuous_driving_hours,
            break_duration_minutes=vehicle.break_duration_minutes,
            mot_expiry=vehicle.mot_expiry,
            tax_due_date=vehicle.tax_due_date,
            insurance_expiry=vehicle.insurance_expiry,
            preferred_driver_id=vehicle.preferred_driver_id,
            depot_id=vehicle.depot_id,
            availability=vehicle.availability,
            initial_maintenance=initial,
            images=images or [],
            documents=documents or [],
        )

    def draft_to_list_item(self, draft: VehicleDraft, vehicle: Vehicle) -> DraftListItem:
        driver = getattr(vehicle, "preferred_driver", None)
        preferred_driver = PreferredDriverSummary(id=driver.id, first_name=driver.first_name, last_name=driver.last_name) if driver is not None else None
        last_edited = max(draft.updated_at, vehicle.updated_at)
        return DraftListItem(
            id=draft.id,
            draft_number=draft.draft_number,
            vehicle_id=vehicle.id,
            registration_number=vehicle.registration_number,
            fleet_number=vehicle.fleet_number,
            fleet_custom_name=vehicle.fleet_custom_name,
            preferred_driver=preferred_driver,
            make=vehicle.make,
            model=vehicle.model,
            year=vehicle.year,
            vehicle_type=vehicle.vehicle_type,
            fuel_type=vehicle.fuel_type,
            average_mpg=vehicle.average_mpg,
            range_miles=vehicle.range_miles,
            cargo_volume_m3=vehicle.cargo_volume_m3,
            max_payload_kg=vehicle.max_payload_kg,
            service_interval_miles=vehicle.service_interval_miles,
            service_interval_months=vehicle.service_interval_months,
            max_continuous_driving_hours=vehicle.max_continuous_driving_hours,
            break_duration_minutes=vehicle.break_duration_minutes,
            availability=vehicle.availability,
            last_edited=last_edited,
        )

    async def require_draft_vehicle_for_publish(self, draft_id: str) -> tuple[VehicleDraft, Vehicle]:
        draft = await self._draft_repo.get_by_id(draft_id)
        if draft is None:
            raise NotFoundError(resource="vehicle_draft", id=draft_id)
        vehicle = await self.get_vehicle(draft.vehicle_id)
        if vehicle.status != VehicleStatus.DRAFT:
            raise ValidationError("This draft has already been published")
        return draft, vehicle

    async def publish_draft(self, draft_id: str, data: UpdateDraftRequest, ctx: AuditContext) -> dict[str, Any]:
        draft, vehicle = await self.require_draft_vehicle_for_publish(draft_id)

        incoming = data.model_dump(exclude_unset=True)
        merged: dict[str, Any] = {}
        for field in CreateVehicleRequest.model_fields:
            if field in incoming:
                merged[field] = incoming[field]
            elif hasattr(vehicle, field):
                merged[field] = getattr(vehicle, field)

        effective_availability = merged.get("availability", vehicle.availability)
        if effective_availability == VehicleAvailability.IN_MAINTENANCE and merged.get("initial_maintenance") is None:
            existing_rec = await self._maintenance_repo.find_latest_by_vehicle_id(vehicle.id)
            if existing_rec is not None:
                merged["initial_maintenance"] = self._maintenance_record_to_log_request(existing_rec).model_dump()

        parsed = CreateVehicleRequest.model_validate(merged)

        today = date.today()
        await self._delete_draft_preview_maintenance(vehicle.id)

        publish_data: dict[str, Any] = {
            **incoming,
            "status": VehicleStatus.ACTIVE,
            "availability": parsed.availability,
            "availability_effective_from": today,
        }
        updated_vehicle = await self._vehicle_repo.update_by_id(draft.vehicle_id, publish_data)

        svc_patch: dict[str, Any] = {}
        v_pub = await self.get_vehicle(draft.vehicle_id)
        m_months = v_pub.service_interval_months
        if isinstance(m_months, int) and m_months > 0:
            svc_patch["next_service_due"] = add_calendar_months(date.today(), m_months)
        cm_pub = v_pub.current_mileage
        svc_patch["last_service_mileage"] = int(cm_pub) if isinstance(cm_pub, int) else None
        if svc_patch:
            updated_vehicle = await self._vehicle_repo.update_by_id(draft.vehicle_id, svc_patch)

        await self._draft_repo.update_by_id(draft.id, {"published_by_id": ctx.user_id})

        if parsed.availability == VehicleAvailability.UNAVAILABLE:
            eff_from = updated_vehicle.availability_effective_from or today
            await self._schedule_repo.create(
                {
                    "vehicle_id": updated_vehicle.id,
                    "date_from": eff_from,
                    "date_to": updated_vehicle.availability_effective_to,
                    "type": ScheduleEventType.UNAVAILABLE,
                    "source": ScheduleEntrySource.AVAILABILITY,
                    "source_id": None,
                    "details": None,
                }
            )

        if parsed.availability == VehicleAvailability.IN_MAINTENANCE and parsed.initial_maintenance is not None:
            await self._persist_initial_maintenance(updated_vehicle.id, parsed.initial_maintenance, ctx)

        updated_vehicle = await self.get_vehicle(updated_vehicle.id)

        image_items = await self.get_draft_all_images(updated_vehicle.id)
        docs = await self._document_repo.find_by_vehicle(updated_vehicle.id)
        doc_responses = [self.document_to_response(d) for d in docs]

        vehicle_payload = await self.to_vehicle_response(updated_vehicle, image_items)
        response_data = CreateVehicleData(**vehicle_payload.model_dump(), documents=doc_responses)

        await self._audit.log(
            action="vehicle.draft_published",
            entity_type="vehicle",
            entity_id=updated_vehicle.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VEHICLE_CREATED,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        logger.info("vehicle.draft_published", draft_id=draft_id, vehicle_id=updated_vehicle.id, availability=parsed.availability)

        return {"data": response_data, "message": "Vehicle published successfully", "vehicle_id": updated_vehicle.id}

    # Compliance

    def _to_percentage_bar(self, *, remaining_days: int | None, total_days: int | None) -> CompliancePercentageBar | None:
        if remaining_days is None:
            return None
        if total_days is None or total_days <= 0:
            if remaining_days <= 0:
                return CompliancePercentageBar(validity_used=100, remaining=0)
            return CompliancePercentageBar(validity_used=0, remaining=100)

        bounded_remaining = min(max(remaining_days, 0), total_days)
        remaining_pct = int(round((bounded_remaining / total_days) * 100))
        validity_used = 100 - remaining_pct
        return CompliancePercentageBar(validity_used=validity_used, remaining=remaining_pct)

    def _to_percentage_bar_miles(self, *, remaining_miles: int | None, total_miles: int | None) -> CompliancePercentageBar | None:
        if remaining_miles is None:
            return None
        if total_miles is None or total_miles <= 0:
            if remaining_miles <= 0:
                return CompliancePercentageBar(validity_used=100, remaining=0)
            return CompliancePercentageBar(validity_used=0, remaining=100)

        bounded_remaining = min(max(remaining_miles, 0), total_miles)
        remaining_pct = int(round((bounded_remaining / total_miles) * 100))
        validity_used = 100 - remaining_pct
        return CompliancePercentageBar(validity_used=validity_used, remaining=remaining_pct)

    async def get_compliance_summary(self, vehicle_id: str) -> dict[str, Any]:
        vehicle = await self.get_vehicle(vehicle_id)
        latest = await self._document_repo.get_latest_by_document_types(
            vehicle_id,
            (DocumentType.MOT, DocumentType.TAX, DocumentType.INSURANCE),
        )
        mot_doc = latest.get(DocumentType.MOT)
        tax_doc = latest.get(DocumentType.TAX)
        ins_doc = latest.get(DocumentType.INSURANCE)

        mot_badge = self._mot_badge(mot_doc.expiry_date if mot_doc else None)
        tax_badge = self._tax_badge(tax_doc.expiry_date if tax_doc else vehicle.tax_due_date)
        ins_badge = self._mot_badge(ins_doc.expiry_date if ins_doc else None)
        cur_mi = vehicle.current_mileage if isinstance(vehicle.current_mileage, int) else None
        int_mi = vehicle.service_interval_miles if isinstance(vehicle.service_interval_miles, int) else None
        last_mi = vehicle.last_service_mileage if isinstance(vehicle.last_service_mileage, int) else None
        rem_miles, rem_days = self._compute_service_remaining(
            next_service_due=vehicle.next_service_due,
            current_mileage=cur_mi,
            service_interval_miles=int_mi,
            last_service_mileage=last_mi,
        )
        svc_badge = self._service_badge(
            vehicle.next_service_due,
            cur_mi,
            int_mi,
            last_mi,
        )
        service_total_days = self._service_interval_total_days(vehicle)
        svc_remaining_days: int | None = None
        svc_remaining_miles: int | None = None
        service_pct: CompliancePercentageBar | None = None
        if svc_badge.display_unit == CardDisplayUnit.MILES:
            svc_remaining_miles = rem_miles
            if rem_miles is not None and int_mi is not None and int_mi > 0:
                service_pct = self._to_percentage_bar_miles(remaining_miles=rem_miles, total_miles=int_mi)
        elif svc_badge.display_unit == CardDisplayUnit.DAYS:
            svc_remaining_days = rem_days
            if rem_days is not None:
                service_pct = self._to_percentage_bar(remaining_days=rem_days, total_days=service_total_days)

        mot_total_days = None
        if mot_doc and mot_doc.expiry_date is not None:
            mot_total_days = max((mot_doc.expiry_date - mot_doc.created_at.date()).days, 1)
        tax_total_days = None
        if tax_doc and tax_doc.expiry_date is not None:
            tax_total_days = max((tax_doc.expiry_date - tax_doc.created_at.date()).days, 1)
        insurance_total_days = None
        if ins_doc and ins_doc.expiry_date is not None:
            insurance_total_days = max((ins_doc.expiry_date - ins_doc.created_at.date()).days, 1)

        return {
            "mot": {
                "status": mot_badge.status,
                "expiry_date": mot_badge.due_date.isoformat() if mot_badge.due_date else None,
                "remaining_days": mot_badge.remaining_days,
                "reference_number": mot_doc.reference_number if mot_doc else None,
                "provider": mot_doc.provider if mot_doc else None,
                "percentage_bar": self._to_percentage_bar(remaining_days=mot_badge.remaining_days, total_days=mot_total_days),
            },
            "tax": {
                "status": tax_badge.status,
                "due_date": tax_badge.due_date.isoformat() if tax_badge.due_date else None,
                "remaining_days": tax_badge.remaining_days,
                "percentage_bar": self._to_percentage_bar(remaining_days=tax_badge.remaining_days, total_days=tax_total_days),
            },
            "insurance": {
                "status": ins_badge.status,
                "expiry_date": ins_badge.due_date.isoformat() if ins_badge.due_date else None,
                "remaining_days": ins_badge.remaining_days,
                "reference_number": ins_doc.reference_number if ins_doc else None,
                "provider": ins_doc.provider if ins_doc else None,
                "percentage_bar": self._to_percentage_bar(remaining_days=ins_badge.remaining_days, total_days=insurance_total_days),
            },
            "service_interval": {
                "status": svc_badge.status,
                "expiry_date": vehicle.next_service_due.isoformat() if vehicle.next_service_due else None,
                "remaining_days": svc_remaining_days,
                "remaining_miles": svc_remaining_miles,
                "display_unit": svc_badge.display_unit,
                "display_value": svc_badge.display_value,
                "percentage_bar": service_pct,
            },
        }

    # Maintenance

    async def _replace_maintenance_schedule_entry(self, vehicle_id: str, record: VehicleMaintenanceRecord) -> None:
        await self._schedule_repo.delete_by_vehicle_source_and_source_id(
            vehicle_id,
            ScheduleEntrySource.MAINTENANCE,
            record.id,
        )
        date_to = record.date_to or record.date_from
        await self._schedule_repo.create(
            {
                "vehicle_id": vehicle_id,
                "date_from": record.date_from,
                "date_to": date_to,
                "type": ScheduleEventType.MAINTENANCE,
                "source": ScheduleEntrySource.MAINTENANCE,
                "source_id": record.id,
                "details": {
                    "maintenance_id": record.id,
                    "maintenance_reference": record.reference,
                    "maintenance_types": record.maintenance_types or [],
                },
            }
        )

    async def log_maintenance(
        self,
        vehicle_id: str,
        data: LogMaintenanceRequest,
        ctx: AuditContext,
    ) -> VehicleMaintenanceRecord:
        await self.get_vehicle(vehicle_id)

        record_data = data.model_dump()
        record_data["vehicle_id"] = vehicle_id
        record_data["recorded_by_id"] = ctx.user_id
        record_data["maintenance_types"] = list(data.maintenance_types)
        record = await self._maintenance_repo.create(record_data)

        await self._replace_maintenance_schedule_entry(vehicle_id, record)

        await self._log_audit(
            action="vehicle.maintenance_logged",
            entity_type="vehicle_maintenance",
            entity_id=record.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={"vehicle_id": vehicle_id, "types": record_data["maintenance_types"]},
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.MAINTENANCE_LOGGED,
        )
        logger.info("vehicle.maintenance_logged", vehicle_id=vehicle_id, record_id=record.id)
        return record

    async def update_maintenance_record(
        self,
        vehicle_id: str,
        record_id: str,
        data: UpdateMaintenanceRecordRequest,
        ctx: AuditContext,
    ) -> VehicleMaintenanceRecord:
        await self.get_vehicle(vehicle_id)
        old = await self._maintenance_repo.get_by_id(record_id)
        if old is None or old.vehicle_id != vehicle_id:
            raise NotFoundError(resource="maintenance", id=record_id)

        patch = data.model_dump(exclude_unset=True)
        if "maintenance_types" in patch:
            patch["maintenance_types"] = list(patch["maintenance_types"])

        eff_from = cast(date, patch.get("date_from", old.date_from))
        eff_to = cast(date | None, patch.get("date_to", old.date_to))
        if eff_from > date.today():
            raise ValidationError("date_from cannot be in the future")
        if eff_to is not None and eff_to < eff_from:
            raise ValidationError("date_to must be on or after date_from")

        updated = await self._maintenance_repo.update_by_id(record_id, patch)
        await self._replace_maintenance_schedule_entry(vehicle_id, updated)

        await self._log_audit(
            action="vehicle.maintenance_updated",
            entity_type="vehicle_maintenance",
            entity_id=record_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"date_from": old.date_from.isoformat(), "date_to": old.date_to.isoformat() if old.date_to else None},
            new_value={k: (v.isoformat() if isinstance(v, date) else v) for k, v in patch.items()},
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VEHICLE_UPDATED,
        )
        logger.info("vehicle.maintenance_updated", vehicle_id=vehicle_id, record_id=record_id)
        return updated

    async def delete_maintenance_record(self, vehicle_id: str, record_id: str, ctx: AuditContext) -> None:
        await self.get_vehicle(vehicle_id)
        old = await self._maintenance_repo.get_by_id(record_id)
        if old is None or old.vehicle_id != vehicle_id:
            raise NotFoundError(resource="maintenance", id=record_id)

        await self._schedule_repo.delete_by_vehicle_source_and_source_id(
            vehicle_id,
            ScheduleEntrySource.MAINTENANCE,
            record_id,
        )
        await self._maintenance_repo.hard_delete(record_id)

        await self._log_audit(
            action="vehicle.maintenance_deleted",
            entity_type="vehicle_maintenance",
            entity_id=record_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"vehicle_id": vehicle_id, "reference": old.reference},
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VEHICLE_UPDATED,
        )
        logger.info("vehicle.maintenance_deleted", vehicle_id=vehicle_id, record_id=record_id)

    async def get_maintenance_record(self, vehicle_id: str, record_id: str) -> VehicleMaintenanceRecord:
        await self.get_vehicle(vehicle_id)
        record = await self._maintenance_repo.get_by_id(record_id)
        if record is None or record.vehicle_id != vehicle_id:
            raise NotFoundError(resource="maintenance", id=record_id)
        return record

    async def get_maintenance_records(
        self,
        vehicle_id: str,
        *,
        page: int = 1,
        size: int = 20,
        maintenance_types: list[str] | None = None,
        search: str | None = None,
    ) -> tuple[list[VehicleMaintenanceRecord], int]:
        await self.get_vehicle(vehicle_id)
        return await self._maintenance_repo.find_by_vehicle(
            vehicle_id,
            page=page,
            size=size,
            maintenance_types=maintenance_types,
            search=search,
        )

    async def get_maintenance_cost_summary(self, vehicle_id: str) -> dict[str, Any]:
        await self.get_vehicle(vehicle_id)
        return await self._maintenance_repo.get_cost_summary(vehicle_id)

    @staticmethod
    def _driver_name_from_route(route: Route) -> tuple[str | None, str | None]:
        did = route.driver_id
        driver = getattr(route, "driver", None)
        if driver is None:
            return did, None
        user = getattr(driver, "user", None)
        if user is None:
            return did, None
        parts = [p for p in (user.first_name, user.last_name) if p]
        return did, (" ".join(parts) if parts else None)

    @staticmethod
    def _route_event_row_dict(e: RouteEvent, route_code: str | None) -> dict[str, Any]:
        def _to_float(value: Any) -> float | None:
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        metadata = getattr(e, "event_metadata", None) or {}
        speed_mph = _to_float(metadata.get("speed_mph"))
        limit_mph = _to_float(metadata.get("limit_mph"))
        speed_over_mph = _to_float(metadata.get("speed_over_mph"))
        if speed_over_mph is None and speed_mph is not None and limit_mph is not None:
            speed_over_mph = speed_mph - limit_mph

        return {
            "id": e.id,
            "route_id": e.route_id,
            "driver_id": e.driver_id,
            "route_code": metadata.get("route_code") or route_code,
            "event_type": e.event_type,
            "occurred_at": e.occurred_at,
            "location_text": metadata.get("location_text"),
            "distance_miles": _to_float(metadata.get("distance_miles")),
            "speed_mph": speed_mph,
            "limit_mph": limit_mph,
            "speed_over_mph": speed_over_mph,
            "start_speed_mph": _to_float(metadata.get("start_speed_mph")),
            "end_speed_mph": _to_float(metadata.get("end_speed_mph")),
            "severity": metadata.get("severity"),
            "lat": e.lat,
            "lng": e.lng,
            "metadata": dict(metadata) if isinstance(metadata, dict) else {},
        }

    async def list_vehicle_routes_history(
        self,
        vehicle_id: str,
        *,
        page: int,
        size: int,
        route_type: list[str] | None = None,
        search: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        vehicle = await self.get_vehicle(vehicle_id)
        sess = self._session
        stmt = select(Route).where(Route.vehicle_id == vehicle_id)
        if route_type:
            normalized = [t.strip().upper() for t in route_type if t and t.strip()]
            allowed = [t for t in normalized if t in {RouteType.PICKUP.value, RouteType.DELIVERY.value}]
            if allowed:
                stmt = stmt.where(Route.route_type.in_(allowed))
        if search:
            like = f"%{search.strip()}%"
            full_name = func.trim(
                func.concat(
                    func.coalesce(User.first_name, ""),
                    " ",
                    func.coalesce(User.last_name, ""),
                )
            )
            stmt = (
                stmt.outerjoin(Driver, Route.driver_id == Driver.id)
                .outerjoin(User, Driver.user_id == User.id)
                .where(
                    or_(
                        Route.route_code.ilike(like),
                        User.first_name.ilike(like),
                        User.last_name.ilike(like),
                        full_name.ilike(like),
                    )
                )
            )
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await sess.execute(count_stmt)).scalar_one()
        order_column = Route.created_at
        offset = (page - 1) * size
        stmt = (
            stmt.order_by(order_column.desc())
            .offset(offset)
            .limit(size)
            .options(
                selectinload(Route.driver).selectinload(Driver.user),
            )
        )
        routes = list((await sess.execute(stmt)).scalars().all())
        out_rows: list[dict[str, Any]] = []
        for r in routes:
            _, driver_name = self._driver_name_from_route(r)
            rt = r.route_type
            type_val = rt.value if isinstance(rt, Enum) else str(rt)
            out_rows.append(
                {
                    "date": r.created_at.date(),
                    "route_id": r.id,
                    "route_code": r.route_code,
                    "driver_name": driver_name,
                    "type": type_val,
                    "estimated_miles": _route_distance_km_to_miles(r.total_distance_km),
                }
            )
        return out_rows, total

    async def get_vehicle_route_detail(self, vehicle_id: str, route_id: str) -> dict[str, Any]:
        await self.get_vehicle(vehicle_id)
        sess = self._session
        route_stmt = (
            select(Route)
            .options(
                selectinload(Route.vehicle),
                selectinload(Route.plan),
                selectinload(Route.driver).selectinload(Driver.user),
            )
            .where(Route.id == route_id)
        )
        route = (await sess.execute(route_stmt)).scalars().first()
        if route is None or route.vehicle_id != vehicle_id:
            raise NotFoundError(resource="route", id=route_id)

        stop_count_stmt = select(func.count()).select_from(RouteStop).where(RouteStop.route_id == route_id)
        counted = int((await sess.execute(stop_count_stmt)).scalar_one() or 0)
        total_stops = route.total_stops or counted
        completed_stmt = (
            select(func.count())
            .select_from(RouteStop)
            .where(RouteStop.route_id == route_id, RouteStop.status == RouteStopStatus.COMPLETED)
        )
        completed = int((await sess.execute(completed_stmt)).scalar_one() or 0)
        percent = int((completed / total_stops) * 100) if total_stops else 0
        svc_date = route.created_at.date()
        _, driver_name = self._driver_name_from_route(route)
        rt = route.route_type
        route_type_str = rt.value if isinstance(rt, Enum) else str(rt)
        st = route.status
        status_str = st.value if isinstance(st, Enum) else str(st)
        telemetry = await self._vehicle_route_telemetry_summary(sess, route_id, route)
        return {
            "route_id": route.id,
            "route_code": route.route_code,
            "route_type": route_type_str,
            "date": svc_date,
            "status": status_str,
            "driver_id": route.driver_id,
            "driver_name": driver_name,
            "vehicle_reg": getattr(route.vehicle, "registration_number", None),
            "estimated_miles": _route_distance_km_to_miles(route.total_distance_km),
            "stops": total_stops,
            "estimated_drive_time_minutes": route.estimated_drive_time_min,
            "actual_drive_time_minutes": route.actual_drive_time_min,
            "progress": {
                "completed_stops": completed,
                "total_stops": total_stops,
                "percent": percent,
            },
            "telemetry": telemetry,
            "encoded_polyline": route.navigation_encoded_polyline,
        }

    async def _vehicle_route_telemetry_summary(
        self,
        sess: AsyncSession,
        route_id: str,
        route: Route,
    ) -> dict[str, Any]:
        agg_stmt = (
            select(RouteEvent.event_type, func.count())
            .where(RouteEvent.route_id == route_id, RouteEvent.event_type.in_(["SPEEDING", "HARSH_BRAKING"]))
            .group_by(RouteEvent.event_type)
        )
        agg_rows = (await sess.execute(agg_stmt)).all()
        counts = {str(r[0]): int(r[1]) for r in agg_rows}
        speeding = counts.get("SPEEDING", 0)
        harsh = counts.get("HARSH_BRAKING", 0)
        meta_stmt = select(RouteEvent.event_metadata).where(
            RouteEvent.route_id == route_id,
            RouteEvent.event_type == "SPEEDING",
        )
        meta_rows = (await sess.execute(meta_stmt)).scalars().all()
        speeds: list[float] = []
        for m in meta_rows:
            if isinstance(m, dict) and m.get("speed_mph") is not None:
                try:
                    speeds.append(float(m["speed_mph"]))
                except (TypeError, ValueError):
                    continue
        max_sp = round(max(speeds), 1) if speeds else None
        avg_mph: float | None = None
        if route.actual_drive_time_min and route.total_distance_km:
            tmin = float(route.actual_drive_time_min)
            if tmin > 0 and route.total_distance_km is not None:
                miles = float(route.total_distance_km) * _KM_TO_MI
                avg_mph = round(miles / (tmin / 60.0), 1)
        return {
            "speeding_events": speeding,
            "harsh_braking_events": harsh,
            "max_speed_mph": max_sp,
            "average_speed_mph": avg_mph,
        }

    @staticmethod
    def _stop_list_tracking_id(
        *,
        dstop: DeliveryStop | None,
        order_row: Order | None,
        flow: Any,
    ) -> str | None:
        if dstop is not None:
            return dstop.tracking_id
        if order_row is not None:
            flow_s = flow.value if isinstance(flow, Enum) else str(flow)
            if flow_s == RouteStopFlowType.PICKUP.value:
                return order_row.master_label_id
            return order_row.order_id
        return None

    @staticmethod
    def _stop_list_label(
        *,
        dstop: DeliveryStop | None,
        pickup_addr: PickupAddress | None,
        order_row: Order | None,
        flow: Any,
    ) -> str | None:
        if dstop is not None:
            postcode = dstop.postcode
            line_1 = dstop.line_1
            first = (dstop.recipient_first_name or "").strip()
            last = (dstop.recipient_last_name or "").strip()
            recipient = " ".join(p for p in (first, last) if p) or None
            parts: list[str] = []
            if postcode:
                parts.append(postcode)
            if recipient or line_1:
                parts.append(str(recipient or line_1 or ""))
            return " – ".join(parts) if parts else None
        if pickup_addr is not None:
            parts = [pickup_addr.postcode, pickup_addr.line_1]
            return " – ".join(p for p in parts if p) or pickup_addr.full_address
        if order_row is not None:
            return order_row.order_id
        return None

    async def list_vehicle_route_stops(
        self,
        vehicle_id: str,
        route_id: str,
        *,
        page: int,
        size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        await self.get_vehicle(vehicle_id)
        sess = self._session
        route = (await sess.execute(select(Route).where(Route.id == route_id))).scalars().first()
        if route is None or route.vehicle_id != vehicle_id:
            raise NotFoundError(resource="route", id=route_id)
        count_stmt = select(func.count()).select_from(RouteStop).where(RouteStop.route_id == route_id)
        total = int((await sess.execute(count_stmt)).scalar_one() or 0)
        offset = (page - 1) * size
        stmt = (
            select(RouteStop, DeliveryStop, Order, PickupAddress)
            .outerjoin(DeliveryStop, RouteStop.delivery_stop_id == DeliveryStop.id)
            .outerjoin(Order, RouteStop.order_id == Order.id)
            .outerjoin(PickupAddress, Order.pickup_address_id == PickupAddress.id)
            .where(RouteStop.route_id == route_id)
            .order_by(RouteStop.sequence.asc())
            .offset(offset)
            .limit(size)
        )
        rows = list((await sess.execute(stmt)).all())
        delivery_ids = [rs.delivery_stop_id for rs, _d, _o, _p in rows if rs.delivery_stop_id]
        note_counts: dict[str, int] = {}
        if delivery_ids:
            nc_stmt = (
                select(StopNote.delivery_stop_id, func.count())
                .where(StopNote.delivery_stop_id.in_(delivery_ids))
                .group_by(StopNote.delivery_stop_id)
            )
            for sid, cnt in (await sess.execute(nc_stmt)).all():
                note_counts[str(sid)] = int(cnt)
        out: list[dict[str, Any]] = []
        for rs, dstop, order_row, pickup_addr in rows:
            flow = rs.stop_flow_type
            flow_s = flow.value if isinstance(flow, Enum) else str(flow)
            st = rs.status
            st_s = st.value if isinstance(st, Enum) else str(st)
            did = rs.delivery_stop_id
            out.append(
                {
                    "route_stop_id": rs.id,
                    "sequence": rs.sequence,
                    "stop_flow_type": flow_s,
                    "status": st_s,
                    "tracking_id": self._stop_list_tracking_id(dstop=dstop, order_row=order_row, flow=flow),
                    "label": self._stop_list_label(dstop=dstop, pickup_addr=pickup_addr, order_row=order_row, flow=flow),
                    "estimated_arrival": rs.estimated_arrival,
                    "actual_arrival": rs.actual_arrival,
                    "notes_count": note_counts.get(did, 0) if did else 0,
                }
            )
        return out, total

    @staticmethod
    def _package_to_vehicle_stop_item(p: Package) -> dict[str, Any]:
        ps = p.status
        status_s = ps.value if isinstance(ps, Enum) else str(ps)
        return {
            "id": p.id,
            "package_id": p.package_id,
            "status": status_s,
            "length_cm": p.length_cm,
            "width_cm": p.width_cm,
            "height_cm": p.height_cm,
            "weight_kg": p.weight_kg or p.declared_weight_kg,
        }

    async def get_vehicle_route_stop_detail(
        self,
        vehicle_id: str,
        route_id: str,
        route_stop_id: str,
    ) -> dict[str, Any]:
        await self.get_vehicle(vehicle_id)
        sess = self._session
        route = (await sess.execute(select(Route).where(Route.id == route_id))).scalars().first()
        if route is None or route.vehicle_id != vehicle_id:
            raise NotFoundError(resource="route", id=route_id)
        rs = (await sess.execute(select(RouteStop).where(RouteStop.id == route_stop_id, RouteStop.route_id == route_id))).scalars().first()
        if rs is None:
            raise NotFoundError(resource="route_stop", id=route_stop_id)
        flow = rs.stop_flow_type
        flow_s = flow.value if isinstance(flow, Enum) else str(flow)
        st = rs.status
        st_s = st.value if isinstance(st, Enum) else str(st)
        packages: list[Package] = []
        tracking_id: str | None = None
        location_label: str | None = None
        postcode: str | None = None
        order_id: str | None = None
        delivery_stop_id: str | None = None
        if flow_s == RouteStopFlowType.PICKUP.value:
            if not rs.order_id:
                raise ValidationError("Pickup route stop has no order_id; cannot load stop details")
            order_id = rs.order_id
            order_row = await sess.get(Order, order_id)
            if order_row is None:
                raise NotFoundError(resource="order", id=order_id)
            pickup_addr = None
            if order_row.pickup_address_id:
                pickup_addr = await sess.get(PickupAddress, order_row.pickup_address_id)
            tracking_id = order_row.master_label_id
            location_label = self._stop_list_label(dstop=None, pickup_addr=pickup_addr, order_row=order_row, flow=flow)
            postcode = pickup_addr.postcode if pickup_addr else None
            pkg_stmt = select(Package).where(Package.order_id == order_id).order_by(Package.created_at.asc())
            packages = list((await sess.execute(pkg_stmt)).scalars().all())
        else:
            if not rs.delivery_stop_id:
                raise ValidationError("Route stop has no delivery_stop_id; cannot load delivery/return details")
            delivery_stop_id = rs.delivery_stop_id
            dstop = await sess.get(DeliveryStop, delivery_stop_id)
            if dstop is None:
                raise NotFoundError(resource="delivery_stop", id=delivery_stop_id)
            order_id = dstop.order_id
            tracking_id = dstop.tracking_id
            location_label = self._stop_list_label(dstop=dstop, pickup_addr=None, order_row=None, flow=flow)
            postcode = dstop.postcode
            pkg_stmt = (
                select(Package)
                .where(Package.delivery_stop_id == delivery_stop_id)
                .order_by(Package.created_at.asc())
            )
            packages = list((await sess.execute(pkg_stmt)).scalars().all())
        weights = [
            float(p.weight_kg) if p.weight_kg is not None
            else float(p.declared_weight_kg or 0)
            for p in packages
            if p.weight_kg is not None or p.declared_weight_kg is not None
        ]
        total_weight = round(sum(weights), 2) if weights else None
        return {
   
            "route_id": route_id,
            "route_stop_id": rs.id,
            "stop_flow_type": flow_s,
            "sequence": rs.sequence,
            "status": st_s,
            "tracking_id": tracking_id,
            "location_label": location_label,
            "postcode": postcode,
            "order_id": order_id,
            "delivery_stop_id": delivery_stop_id,
            "scheduled_at": rs.estimated_arrival,
            "actual_at": rs.actual_arrival,
            "total_packages": len(packages),
            "total_weight_kg": total_weight,
            "packages": [self._package_to_vehicle_stop_item(p) for p in packages],
        }

    async def list_vehicle_route_speeding_events(self, vehicle_id: str, route_id: str) -> list[dict[str, Any]]:
        return await self._list_vehicle_route_events_by_type(vehicle_id, route_id, "SPEEDING")

    async def list_vehicle_route_harsh_braking_events(self, vehicle_id: str, route_id: str) -> list[dict[str, Any]]:
        return await self._list_vehicle_route_events_by_type(vehicle_id, route_id, "HARSH_BRAKING")

    async def _list_vehicle_route_events_by_type(
        self,
        vehicle_id: str,
        route_id: str,
        event_type: str,
    ) -> list[dict[str, Any]]:
        await self.get_vehicle(vehicle_id)
        sess = self._session
        rid_stmt = select(Route.vehicle_id).where(Route.id == route_id)
        row_vid = (await sess.execute(rid_stmt)).scalar_one_or_none()
        if row_vid is None or row_vid != vehicle_id:
            raise NotFoundError(resource="route", id=route_id)
        stmt = (
            select(RouteEvent, Route.route_code)
            .join(Route, Route.id == RouteEvent.route_id)
            .where(RouteEvent.route_id == route_id, RouteEvent.event_type == event_type)
            .order_by(RouteEvent.occurred_at.desc())
        )
        events = list((await sess.execute(stmt)).all())
        return [self._route_event_row_dict(e, route_code) for e, route_code in events]

    async def _build_stop_note_entries(self, *, delivery_stop_id: str, order_id: str) -> list[StopNoteEntry]:
        notes = await self._stop_note_repo.list_for_delivery_stop(delivery_stop_id)
        note_ids = [n.id for n in notes]
        images = await self._stop_note_repo.list_images_for_note_ids(note_ids)
        images_by_note: dict[str, list[StopNoteImageEntry]] = {}
        for image in images:
            images_by_note.setdefault(image.stop_note_id, []).append(
                StopNoteImageEntry(
                    id=image.id,
                    stop_note_id=image.stop_note_id,
                    image_key=image.image_key,
                    image_url=generate_image_url(image.image_key),
                    sort_order=image.sort_order,
                    created_at=image.created_at,
                    updated_at=image.updated_at,
                    version=image.version,
                )
            )
        pkg_by_note = await batch_package_ids_for_stop_notes(
            self._session,
            delivery_stop_id=delivery_stop_id,
            order_id=order_id,
            notes=cast(list[object], notes),
        )
        return [
            StopNoteEntry(
                id=note.id,
                delivery_stop_id=note.delivery_stop_id,
                note_type=note.note_type,
                message=note.message,
                is_blocking=note.is_blocking,
                sort_order=note.sort_order,
                package_ids=pkg_by_note.get(note.id, []),
                images=images_by_note.get(note.id, []),
                created_at=note.created_at,
                updated_at=note.updated_at,
                version=note.version,
            )
            for note in notes
        ]

    async def get_vehicle_route_notes(self, vehicle_id: str, route_id: str) -> dict[str, Any]:
        await self.get_vehicle(vehicle_id)
        sess = self._session
        route = (await sess.execute(select(Route).where(Route.id == route_id))).scalars().first()
        if route is None or route.vehicle_id != vehicle_id:
            raise NotFoundError(resource="route", id=route_id)
        stops_stmt = select(RouteStop).where(RouteStop.route_id == route_id).order_by(RouteStop.sequence.asc())
        route_stops = list((await sess.execute(stops_stmt)).scalars().all())
        stops_out: list[dict[str, Any]] = []
        for rs in route_stops:
            notes_list: list[StopNoteEntry] = []
            if rs.delivery_stop_id:
                dstop = await sess.get(DeliveryStop, rs.delivery_stop_id)
                if dstop is not None:
                    notes_list = await self._build_stop_note_entries(
                        delivery_stop_id=rs.delivery_stop_id,
                        order_id=dstop.order_id,
                    )
            stops_out.append(
                {
                    "route_stop_id": rs.id,
                    "sequence": rs.sequence,
                    "delivery_stop_id": rs.delivery_stop_id,
                    "notes": notes_list,
                }
            )
        return {"route_id": route_id, "stops": stops_out}

    async def get_schedule(
        self,
        vehicle_id: str,
        start_date: date,
        end_date: date,
        *,
        event_types: list[ScheduleCalendarFilterKind] | None = None,
    ) -> ScheduleResponse:
        await self.get_vehicle(vehicle_id)
        total_days = (end_date - start_date).days + 1

        ranges = await self._schedule_repo.find_by_vehicle_and_date_range(vehicle_id, start_date, end_date)
        by_date_entry: dict[date, VehicleScheduleEntry] = {}
        for r in ranges:
            d_from = max(r.date_from, start_date)
            d_to = min(r.date_to if r.date_to is not None else end_date, end_date)
            d = d_from
            while d <= d_to:
                if d not in by_date_entry or self._schedule_priority(r.type) > self._schedule_priority(by_date_entry[d].type):
                    by_date_entry[d] = r
                d += timedelta(days=1)

        route_rows = await self._route_calendar_repo.list_routes_with_service_date(vehicle_id, start_date, end_date)
        by_date_route = self._collapse_routes_per_calendar_day(route_rows)

        events: list[ScheduleEvent] = []
        d = start_date
        while d <= end_date:
            events.append(self._merge_schedule_day(d, by_date_entry.get(d), by_date_route.get(d)))
            d += timedelta(days=1)

        await self._enrich_schedule_maintenance_details(vehicle_id, events)
        summary = self._build_utilization_summary(events, total_days)
        if event_types:
            allowed = self._schedule_calendar_filters_to_event_types(event_types)
            events = [e for e in events if e.type in allowed]
        return ScheduleResponse(events=events, utilization_summary=summary)

    def _schedule_calendar_filters_to_event_types(
        self,
        kinds: list[ScheduleCalendarFilterKind],
    ) -> frozenset[ScheduleEventType]:
        expanded: set[ScheduleEventType] = set()
        for k in kinds:
            if k == ScheduleCalendarFilterKind.DELIVERY_ROUTE:
                expanded.update(
                    (
                        ScheduleEventType.COMPLETED_DELIVERY,
                        ScheduleEventType.OUT_FOR_DELIVERY,
                    )
                )
            elif k == ScheduleCalendarFilterKind.PICKUP_ROUTE:
                expanded.update(
                    (
                        ScheduleEventType.COMPLETED_PICKUP,
                        ScheduleEventType.OUT_FOR_PICKUP,
                    )
                )
            elif k == ScheduleCalendarFilterKind.MAINTENANCE:
                expanded.add(ScheduleEventType.MAINTENANCE)
            elif k == ScheduleCalendarFilterKind.UNAVAILABLE:
                expanded.add(ScheduleEventType.UNAVAILABLE)
        return frozenset(expanded)

    def _collapse_routes_per_calendar_day(self, rows: list[tuple[Route, date]]) -> dict[date, Route]:
        out: dict[date, Route] = {}
        for route, svc_date in rows:
            if str(route.status).upper() == RouteStatus.DRAFT.value:
                continue
            if svc_date not in out:
                out[svc_date] = route
            else:
                out[svc_date] = self._pick_better_route_for_day(out[svc_date], route)
        return out

    def _pick_better_route_for_day(self, current: Route, incoming: Route) -> Route:
        ra = self._route_status_rank(str(current.status))
        rb = self._route_status_rank(str(incoming.status))
        if rb < ra:
            return incoming
        if ra < rb:
            return current
        ua = current.updated_at or current.created_at
        ub = incoming.updated_at or incoming.created_at
        return incoming if ub >= ua else current

    def _route_status_rank(self, status: str) -> int:
        u = status.upper()
        if u == RouteStatus.ACTIVE.value:
            return 0
        if u == RouteStatus.ASSIGNED.value:
            return 1
        if u == RouteStatus.COMPLETED.value:
            return 2
        return 3

    def _route_to_schedule_type(self, route: Route) -> ScheduleEventType | None:
        st = str(route.status).upper()
        rt = str(route.route_type).upper()
        if st == RouteStatus.DRAFT.value:
            return None
        if st == RouteStatus.COMPLETED.value:
            return ScheduleEventType.COMPLETED_PICKUP if rt == RouteType.PICKUP.value else ScheduleEventType.COMPLETED_DELIVERY
        if st in (RouteStatus.ACTIVE.value, RouteStatus.ASSIGNED.value):
            return ScheduleEventType.OUT_FOR_PICKUP if rt == RouteType.PICKUP.value else ScheduleEventType.OUT_FOR_DELIVERY
        return None

    def _route_status_display(self, route: Route, event_type: ScheduleEventType) -> str:
        rt = str(route.route_type).upper()
        if rt == RouteType.DELIVERY.value:
            label = "Delivery"
        elif rt == RouteType.PICKUP.value:
            label = "Pickup"
        else:
            label = str(route.route_type).title()
        if event_type in (ScheduleEventType.OUT_FOR_DELIVERY, ScheduleEventType.OUT_FOR_PICKUP):
            return f"Out for {label}"
        if event_type in (ScheduleEventType.COMPLETED_DELIVERY, ScheduleEventType.COMPLETED_PICKUP):
            return f"Completed {label}"
        return str(route.status).title()

    def _driver_display_name(self, route: Route) -> str | None:
        driver = route.driver
        if driver is None:
            return None
        user = driver.user
        if user is None:
            return None
        parts = [p for p in (user.first_name, user.last_name) if p]
        if not parts:
            return None
        return " ".join(parts)

    def _normalized_entry_schedule_type(self, entry: VehicleScheduleEntry) -> ScheduleEventType:
        if entry.type != ScheduleEventType.COMPLETED:
            return entry.type
        return self._completed_split_from_details(entry.details)

    def _completed_split_from_details(self, details: dict[str, Any] | None) -> ScheduleEventType:
        if details:
            raw_rt = details.get("route_type")
            if raw_rt is not None and str(raw_rt).upper() == RouteType.PICKUP.value:
                return ScheduleEventType.COMPLETED_PICKUP
        return ScheduleEventType.COMPLETED_DELIVERY

    def _schedule_event_from_entry(self, d: date, entry: VehicleScheduleEntry) -> ScheduleEvent:
        ntype = self._normalized_entry_schedule_type(entry)
        details: ScheduleEventDetails | None = None
        if entry.details:
            raw = entry.details
            stops_raw = raw.get("stops_count")
            if stops_raw is None:
                stops_raw = raw.get("stops")
            stops_count: int | None = None
            if stops_raw is not None:
                try:
                    stops_count = int(stops_raw)
                except (TypeError, ValueError):
                    stops_count = None
            details = ScheduleEventDetails(
                maintenance_id=raw.get("maintenance_id"),
                maintenance_types=raw.get("maintenance_types"),
                route_id=raw.get("route_id"),
                route_code=raw.get("route_code"),
                route_type=raw.get("route_type"),
                status_label=raw.get("status_label"),
                driver_name=raw.get("driver_name"),
                stops_count=stops_count,
                extra=raw.get("extra"),
            )
        elif entry.source_id and entry.source == ScheduleEntrySource.MAINTENANCE:
            details = ScheduleEventDetails(maintenance_id=entry.source_id)
        return ScheduleEvent(date=d, type=ntype, details=details)

    def _merge_schedule_day(
        self,
        d: date,
        entry: VehicleScheduleEntry | None,
        route: Route | None,
    ) -> ScheduleEvent:
        route_ev: ScheduleEvent | None = None
        if route is not None:
            rt = self._route_to_schedule_type(route)
            if rt is not None:
                route_ev = ScheduleEvent(
                    date=d,
                    type=rt,
                    details=ScheduleEventDetails(
                        route_id=route.id,
                        route_code=route.route_code,
                        route_type=route.route_type,
                        status_label=self._route_status_display(route, rt),
                        driver_name=self._driver_display_name(route),
                        stops_count=route.total_stops,
                    ),
                )
        entry_ev: ScheduleEvent | None = None
        if entry is not None:
            entry_ev = self._schedule_event_from_entry(d, entry)
        if route_ev is None and entry_ev is None:
            return ScheduleEvent(date=d, type=ScheduleEventType.AVAILABLE, details=None)
        if route_ev is None:
            assert entry_ev is not None
            return entry_ev
        if entry_ev is None:
            return route_ev
        pe = self._schedule_priority(entry_ev.type)
        pr = self._schedule_priority(route_ev.type)
        if pr > pe:
            return route_ev
        if pe > pr:
            return entry_ev
        return route_ev

    async def _enrich_schedule_maintenance_details(self, vehicle_id: str, events: list[ScheduleEvent]) -> None:
        ids = {e.details.maintenance_id for e in events if e.details and e.details.maintenance_id}
        if not ids:
            return
        stmt = select(VehicleMaintenanceRecord).where(
            VehicleMaintenanceRecord.id.in_(ids),
            VehicleMaintenanceRecord.vehicle_id == vehicle_id,
        )
        result = await self._session.execute(stmt)
        by_id = {rec.id: rec for rec in result.scalars().all()}
        for e in events:
            if e.details is None or e.details.maintenance_id is None:
                continue
            rec = by_id.get(e.details.maintenance_id)
            if rec is None:
                continue
            desc = self._format_maintenance_description(rec.maintenance_types)
            mt_list = [str(x) for x in rec.maintenance_types] if rec.maintenance_types else None
            e.details = e.details.model_copy(
                update={
                    "maintenance_reference": rec.reference,
                    "maintenance_types": mt_list,
                    "maintenance_description": desc,
                }
            )

    def _format_maintenance_description(self, types: list[Any] | None) -> str | None:
        if not types:
            return None
        labels: list[str] = []
        for t in types:
            key = t if isinstance(t, str) else getattr(t, "value", str(t))
            try:
                labels.append(str(key).replace("_", " ").title())
            except ValueError:
                labels.append(str(key))
        return ", ".join(labels)

    def _build_utilization_summary(self, events: list[ScheduleEvent], total_days: int) -> UtilizationSummary:
        def pct(n: int) -> int:
            if total_days <= 0:
                return 0
            return int(round(100.0 * n / total_days))

        c_del = sum(1 for e in events if e.type == ScheduleEventType.COMPLETED_DELIVERY)
        c_pic = sum(1 for e in events if e.type == ScheduleEventType.COMPLETED_PICKUP)
        o_del = sum(1 for e in events if e.type == ScheduleEventType.OUT_FOR_DELIVERY)
        o_pic = sum(1 for e in events if e.type == ScheduleEventType.OUT_FOR_PICKUP)
        maint = sum(1 for e in events if e.type == ScheduleEventType.MAINTENANCE)
        unav = sum(1 for e in events if e.type == ScheduleEventType.UNAVAILABLE)
        av = sum(1 for e in events if e.type == ScheduleEventType.AVAILABLE)
        return UtilizationSummary(
            completed_delivery_days=c_del,
            completed_delivery_percent=pct(c_del),
            completed_pickup_days=c_pic,
            completed_pickup_percent=pct(c_pic),
            out_for_delivery_days=o_del,
            out_for_delivery_percent=pct(o_del),
            out_for_pickup_days=o_pic,
            out_for_pickup_percent=pct(o_pic),
            maintenance_days=maint,
            maintenance_percent=pct(maint),
            unavailable_days=unav,
            unavailable_percent=pct(unav),
            available_days=av,
            available_percent=pct(av),
        )

    def _schedule_priority(self, t: ScheduleEventType) -> int:
        if t == ScheduleEventType.AVAILABLE:
            return -1
        if t in (ScheduleEventType.OUT_FOR_DELIVERY, ScheduleEventType.OUT_FOR_PICKUP):
            return 0
        if t in (
            ScheduleEventType.COMPLETED,
            ScheduleEventType.COMPLETED_DELIVERY,
            ScheduleEventType.COMPLETED_PICKUP,
        ):
            return 1
        if t == ScheduleEventType.MAINTENANCE:
            return 2
        if t == ScheduleEventType.UNAVAILABLE:
            return 3
        return -1

    # Defects

    async def report_defect(
        self,
        vehicle_id: str,
        data: ReportDefectRequest,
        ctx: AuditContext,
        image_files: list[tuple[bytes, str, str]] | None = None,
    ) -> tuple[DefectResponse, list[BulkUploadFailure]]:
        await self.get_vehicle(vehicle_id)

        defect_data = data.model_dump()
        defect_data["vehicle_id"] = vehicle_id

        defect = await self._defect_repo.create(defect_data)
        upload_failures: list[BulkUploadFailure] = []

        if image_files:
            upload_items = [(content, filename, {"vehicle_id": vehicle_id, "defect_id": defect.id}) for content, filename, _ in image_files]
            result: BulkUploadResult[Any] = await bulk_upload_images(upload_items, raise_if_all_failed=False)

            for _idx, cf_result in sorted(result.succeeded, key=lambda x: x[0]):
                await self._defect_image_repo.create(
                    {
                        "defect_id": defect.id,
                        "file_path": cf_result.id,
                        "uploaded_by_id": ctx.user_id,
                    }
                )

            upload_failures = [BulkUploadFailure(index=idx, message=msg) for idx, msg in result.failed]
            for f in upload_failures:
                logger.warning(
                    "vehicle.defect_image_upload_failed",
                    vehicle_id=vehicle_id,
                    defect_id=defect.id,
                    index=f.index,
                    error=f.message,
                )

        await self._log_audit(
            action="vehicle.defect_reported",
            entity_type="vehicle_defect",
            entity_id=defect.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={
                "vehicle_id": vehicle_id,
                "category": data.category,
                "severity": data.severity.value,
            },
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.DEFECT_REPORTED,
        )
        logger.info("vehicle.defect_reported", vehicle_id=vehicle_id, defect_id=defect.id)

        defect_loaded = await self._defect_repo.get_by_id_with_images(defect.id)
        assert defect_loaded is not None
        return self.defect_to_response(defect_loaded), upload_failures

    async def update_defect(
        self,
        vehicle_id: str,
        defect_id: str,
        data: UpdateDefectRequest,
        ctx: AuditContext,
    ) -> DefectResponse:
        await self.get_vehicle(vehicle_id)

        old_defect = await self._defect_repo.get_by_id(defect_id)
        if old_defect is None or old_defect.vehicle_id != vehicle_id:
            raise NotFoundError(resource="defect", id=defect_id)

        patch = data.model_dump(exclude_unset=True)
        if "reported_by_id" in patch and patch["reported_by_id"] is not None:
            patch["reported_by_id"] = str(patch["reported_by_id"])
        for enum_key in ("status", "category", "severity"):
            if enum_key in patch and hasattr(patch[enum_key], "value"):
                patch[enum_key] = patch[enum_key].value

        await self._defect_repo.update_by_id(defect_id, patch)

        st = patch.get("status")
        event_type = AuditEventType.DEFECT_RESOLVED if st == DefectStatus.RESOLVED.value else AuditEventType.VEHICLE_UPDATED
        audit_new: dict[str, Any] = {}
        for k, v in patch.items():
            if isinstance(v, date):
                audit_new[k] = v.isoformat()
            else:
                audit_new[k] = v
        await self._log_audit(
            action="vehicle.defect_updated",
            entity_type="vehicle_defect",
            entity_id=defect_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"status": old_defect.status.value},
            new_value=audit_new,
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=event_type,
        )
        logger.info("vehicle.defect_updated", defect_id=defect_id, vehicle_id=vehicle_id)

        defect = await self._defect_repo.get_by_id_with_images(defect_id)
        assert defect is not None
        return self.defect_to_response(defect)

    async def delete_defect(self, vehicle_id: str, defect_id: str, ctx: AuditContext) -> None:
        await self.get_vehicle(vehicle_id)
        defect = await self._defect_repo.get_by_id_with_images(defect_id)
        if defect is None or defect.vehicle_id != vehicle_id:
            raise NotFoundError(resource="defect", id=defect_id)

        for img in defect.images or []:
            if img.file_path:
                try:
                    await delete_image(img.file_path)
                except Exception:
                    logger.warning("vehicle.defect_image_r2_cleanup_failed", vehicle_id=vehicle_id, defect_id=defect_id, file_path=img.file_path)

        await self._defect_repo.hard_delete(defect_id)

        await self._log_audit(
            action="vehicle.defect_deleted",
            entity_type="vehicle_defect",
            entity_id=defect_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"vehicle_id": vehicle_id, "reference": defect.reference},
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VEHICLE_UPDATED,
        )
        logger.info("vehicle.defect_deleted", vehicle_id=vehicle_id, defect_id=defect_id)

    async def get_defects(
        self,
        vehicle_id: str,
        *,
        page: int = 1,
        size: int = 20,
        statuses: list[DefectStatus] | None = None,
        search: str | None = None,
    ) -> tuple[list[VehicleDefect], int]:
        await self.get_vehicle(vehicle_id)
        return await self._defect_repo.find_by_vehicle_with_images(
            vehicle_id,
            page=page,
            size=size,
            statuses=statuses,
            search=search,
        )

    async def get_open_defect_count(self, vehicle_id: str) -> int:
        return await self._defect_repo.count_open_by_vehicle(vehicle_id)

    # Service Records

    def _next_service_due_from_vehicle_interval(self, vehicle: Vehicle, service_date: date) -> date | None:
        months = vehicle.service_interval_months
        if isinstance(months, int) and months > 0:
            return add_calendar_months(service_date, months)
        return None

    @staticmethod
    def _next_service_due_after_month_interval_change(
        *,
        previous_due: date | None,
        previous_months: int | None,
        new_months: int,
    ) -> date:
        if previous_due is not None and isinstance(previous_months, int) and previous_months > 0:
            anchor = add_calendar_months(previous_due, -previous_months)
            return add_calendar_months(anchor, new_months)
        return add_calendar_months(date.today(), new_months)

    async def _sync_vehicle_service_after_service_mutation(self, vehicle_id: str) -> None:
        latest_due = await self._service_repo.latest_next_service_due_for_vehicle(vehicle_id)
        latest_m = await self._service_repo.latest_mileage_at_service_for_vehicle(vehicle_id)
        v = await self.get_vehicle(vehicle_id)
        if latest_due is None:
            latest_due = self._next_service_due_from_vehicle_interval(v, date.today())
        await self._vehicle_repo.update_by_id(
            vehicle_id,
            {
                "next_service_due": latest_due,
                "last_service_mileage": latest_m,
                "driver_service_alert_sent_at": None,
            },
        )

    @staticmethod
    def _mileage_service_due_threshold(vehicle: Vehicle) -> int | None:
        int_mi = vehicle.service_interval_miles if isinstance(vehicle.service_interval_miles, int) else None
        last_mi = vehicle.last_service_mileage if isinstance(vehicle.last_service_mileage, int) else None
        if int_mi is not None and int_mi > 0 and last_mi is not None:
            return last_mi + int_mi
        return None

    async def evaluate_driver_service_due_alerts_for_vehicle(self, vehicle: Vehicle, *, today: date) -> int:
        if vehicle.status != VehicleStatus.ACTIVE or vehicle.preferred_driver_id is None:
            return 0
        if vehicle.driver_service_alert_sent_at is not None:
            return 0

        calendar_due = vehicle.next_service_due is not None and vehicle.next_service_due <= today
        threshold = self._mileage_service_due_threshold(vehicle)
        cur_mi = vehicle.current_mileage if isinstance(vehicle.current_mileage, int) else None
        mileage_due = threshold is not None and cur_mi is not None and cur_mi >= threshold
        if not (calendar_due or mileage_due):
            return 0

        int_mi = vehicle.service_interval_miles if isinstance(vehicle.service_interval_miles, int) else None
        last_mi = vehicle.last_service_mileage if isinstance(vehicle.last_service_mileage, int) else None
        rem_miles, rem_days = self._compute_service_remaining(
            next_service_due=vehicle.next_service_due,
            current_mileage=cur_mi,
            service_interval_miles=int_mi,
            last_service_mileage=last_mi,
        )
        if calendar_due and mileage_due:
            unit, _, _ = self._pick_next_service_display(rem_miles, rem_days)
            kind = "calendar" if unit == CardDisplayUnit.DAYS else "mileage"
        elif calendar_due:
            kind = "calendar"
        else:
            kind = "mileage"

        driver_id = vehicle.preferred_driver_id
        user = await self._user_repo.get_by_id(driver_id)
        if user is None:
            logger.warning(
                "vehicle.driver_service_due_skip_no_user",
                vehicle_id=vehicle.id,
                driver_user_id=driver_id,
            )
            return 0

        nd_str = vehicle.next_service_due.isoformat() if vehicle.next_service_due else ""
        reg = (vehicle.registration_number or "").strip() or "no registration shown"
        vref = f"{vehicle.fleet_number} ({reg})"
        if kind == "calendar":
            assert vehicle.next_service_due is not None
            if vehicle.next_service_due < today:
                detail = f"{vref}: Vehicle service is overdue. Please arrange a garage visit."
            else:
                detail = f"{vref}: Vehicle service is due today. Please arrange a garage visit."
        else:
            assert cur_mi is not None and threshold is not None
            detail = f"{vref}: Vehicle service is due by mileage. Please arrange a garage visit."

        ctx = {
            "fleet_number": vehicle.fleet_number,
            "registration_number": vehicle.registration_number or "",
            "due_kind": kind,
            "due_detail": detail,
            "next_service_due": nd_str,
            "mileage_target": str(threshold) if threshold is not None else "",
            "current_mileage": str(cur_mi) if cur_mi is not None else "",
        }
        sent = await notify(
            event=NotificationEvent.DRIVER_VEHICLE_SERVICE_DUE,
            notification_type=NotificationType.DRIVER,
            organization_id=user.organization_id,
            user_id=driver_id,
            context=ctx,
        )
        if not sent:
            return 0

        await self._vehicle_repo.update_by_id(
            vehicle.id,
            {"driver_service_alert_sent_at": datetime.now(UTC)},
        )
        return 1

    async def evaluate_driver_service_due_alerts_for_vehicle_id(self, vehicle_id: str, *, today: date) -> int:
        v = await self._vehicle_repo.get_by_id(vehicle_id)
        if v is None:
            return 0
        return await self.evaluate_driver_service_due_alerts_for_vehicle(v, today=today)

    async def run_daily_driver_service_due_evaluation(self, today: date) -> tuple[int, int]:
        stmt = (
            select(Vehicle.id)
            .where(
                Vehicle.status == VehicleStatus.ACTIVE,
                Vehicle.preferred_driver_id.isnot(None),
                Vehicle.driver_service_alert_sent_at.is_(None),
                or_(
                    and_(Vehicle.next_service_due.isnot(None), Vehicle.next_service_due <= today),
                    and_(
                        Vehicle.service_interval_miles.isnot(None),
                        Vehicle.service_interval_miles > 0,
                        Vehicle.last_service_mileage.isnot(None),
                        Vehicle.current_mileage.isnot(None),
                        Vehicle.current_mileage >= Vehicle.last_service_mileage + Vehicle.service_interval_miles,
                    ),
                ),
            )
        )
        result = await self._session.execute(stmt)
        ids = list(result.scalars().all())
        notifications = 0
        for vid in ids:
            notifications += await self.evaluate_driver_service_due_alerts_for_vehicle_id(vid, today=today)
        return len(ids), notifications

    async def add_service_record(
        self,
        vehicle_id: str,
        data: AddServiceRecordRequest,
        ctx: AuditContext,
    ) -> VehicleServiceRecord:
        vehicle = await self.get_vehicle(vehicle_id)

        next_due = data.next_service_due
        if next_due is None:
            next_due = self._next_service_due_from_vehicle_interval(vehicle, data.service_date)

        miles_at = data.mileage_at_service
        if miles_at is None:
            miles_at = int(vehicle.current_mileage) if isinstance(vehicle.current_mileage, int) else 0

        record_data = {
            "vehicle_id": vehicle_id,
            "service_date": data.service_date,
            "service_type": data.service_type,
            "next_service_due": next_due,
            "mileage_at_service": miles_at,
            "cost": data.cost,
            "status": data.status,
            "notes": data.notes,
        }

        record = await self._service_repo.create(record_data)
        await self._sync_vehicle_service_after_service_mutation(vehicle_id)

        await self._log_audit(
            action="vehicle.service_recorded",
            entity_type="vehicle_service",
            entity_id=record.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={
                "vehicle_id": vehicle_id,
                "service_type": data.service_type,
            },
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.MAINTENANCE_LOGGED,
        )
        logger.info("vehicle.service_recorded", vehicle_id=vehicle_id, record_id=record.id)
        return record

    async def update_service_record(
        self,
        vehicle_id: str,
        record_id: str,
        data: UpdateServiceRecordRequest,
        ctx: AuditContext,
    ) -> VehicleServiceRecord:
        vehicle = await self.get_vehicle(vehicle_id)
        record = await self._service_repo.get_by_id(record_id)
        if record is None or record.vehicle_id != vehicle_id:
            raise NotFoundError(resource="service_record", id=record_id)

        patch = data.model_dump(exclude_unset=True)
        for enum_key in ("service_type", "status"):
            if enum_key in patch and hasattr(patch[enum_key], "value"):
                patch[enum_key] = patch[enum_key].value

        if "service_date" in patch and "next_service_due" not in patch:
            sd = cast(date, patch["service_date"])
            computed = self._next_service_due_from_vehicle_interval(vehicle, sd)
            if computed is not None:
                patch["next_service_due"] = computed

        merged_date = patch.get("service_date", record.service_date)
        merged_next = patch.get("next_service_due", record.next_service_due)
        if merged_next is not None and merged_date is not None and merged_next <= merged_date:
            raise ValidationError("next_service_due must be after service_date")

        old_for_audit: dict[str, Any] = {
            "service_date": record.service_date.isoformat() if record.service_date else None,
            "service_type": record.service_type,
            "next_service_due": record.next_service_due.isoformat() if record.next_service_due else None,
            "cost": record.cost,
            "status": record.status.value,
            "notes": record.notes,
        }

        updated = await self._service_repo.update_by_id(record_id, patch)

        await self._sync_vehicle_service_after_service_mutation(vehicle_id)

        new_for_audit: dict[str, Any] = {}
        for k, v in patch.items():
            if isinstance(v, date):
                new_for_audit[k] = v.isoformat()
            elif isinstance(v, Enum):
                new_for_audit[k] = v.value
            else:
                new_for_audit[k] = v

        await self._audit.log(
            action="vehicle.service_record_updated",
            entity_type="vehicle_service",
            entity_id=record_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value=old_for_audit,
            new_value=new_for_audit,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        logger.info("vehicle.service_record_updated", vehicle_id=vehicle_id, record_id=record_id)
        return updated

    async def get_service_records(
        self,
        vehicle_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[VehicleServiceRecord], int]:
        await self.get_vehicle(vehicle_id)
        return await self._service_repo.find_by_vehicle(vehicle_id, page=page, size=size)

    async def delete_service_record(self, vehicle_id: str, record_id: str, ctx: AuditContext) -> None:
        await self.get_vehicle(vehicle_id)
        record = await self._service_repo.get_by_id(record_id)
        if record is None or record.vehicle_id != vehicle_id:
            raise NotFoundError(resource="service_record", id=record_id)

        await self._service_repo.hard_delete(record_id)
        await self._sync_vehicle_service_after_service_mutation(vehicle_id)

        await self._log_audit(
            action="vehicle.service_deleted",
            entity_type="vehicle_service",
            entity_id=record_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"vehicle_id": vehicle_id, "service_type": record.service_type},
            severity="WARNING",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.MAINTENANCE_LOGGED,
        )

    # Documents

    async def _sync_vehicle_compliance_cache(self, vehicle_id: str) -> None:
        docs = await self._document_repo.find_by_vehicle(vehicle_id)
        mot_docs = [d for d in docs if d.document_type == DocumentType.MOT]
        tax_docs = [d for d in docs if d.document_type == DocumentType.TAX]
        ins_docs = [d for d in docs if d.document_type == DocumentType.INSURANCE]

        def current(doc_list: list[VehicleDocument]) -> VehicleDocument | None:
            if not doc_list:
                return None
            return max(doc_list, key=lambda d: (d.expiry_date or date.min, d.created_at))

        mot = current(mot_docs)
        tax = current(tax_docs)
        ins = current(ins_docs)

        update: dict[str, Any] = {
            "mot_expiry": mot.expiry_date if mot else None,
            "tax_due_date": tax.expiry_date if tax else None,
            "insurance_expiry": ins.expiry_date if ins else None,
        }
        vehicle = await self._vehicle_repo.get_by_id(vehicle_id)
        if vehicle is not None:
            await self._vehicle_repo.update_by_id(vehicle_id, update)

    async def add_document(
        self,
        vehicle_id: str,
        metadata: UploadDocumentRequest,
        file: tuple[bytes, str, str],
        ctx: AuditContext,
    ) -> VehicleDocument:
        """Upload a single document for a vehicle."""
        await self.get_vehicle(vehicle_id)

        file_content, file_name, content_type = file
        ext = file_name.rsplit(".", 1)[-1] if "." in file_name else "bin"
        r2_key = f"vehicles/{vehicle_id}/documents/{uuid4().hex}.{ext}"

        await upload_to_r2(r2_key, file_content, content_type)

        doc_data = metadata.model_dump()
        doc_data["vehicle_id"] = vehicle_id
        doc_data["uploaded_by_id"] = ctx.user_id
        doc_data["file_path"] = r2_key

        document = await self._document_repo.create(doc_data)

        await self._log_audit(
            action="vehicle.document_uploaded",
            entity_type="vehicle_document",
            entity_id=document.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={
                "vehicle_id": vehicle_id,
                "document_type": metadata.document_type.value,
                "title": metadata.title,
            },
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.DOCUMENT_UPLOADED,
        )
        logger.info(
            "vehicle.document_uploaded",
            vehicle_id=vehicle_id,
            doc_id=document.id,
        )

        await self._sync_vehicle_compliance_cache(vehicle_id)
        return document

    async def add_documents_bulk(
        self,
        vehicle_id: str,
        docs: list[tuple[int, UploadDocumentRequest, tuple[bytes, str, str]]],
        ctx: AuditContext,
    ) -> BulkDocumentUploadOutcome:
        await self.get_vehicle(vehicle_id)

        # Bulk upload first, then create DB records only for successful uploads.
        upload_items: list[tuple[str, bytes, str]] = []
        bulk_index_to_original_index: list[int] = []
        bulk_index_to_meta: list[UploadDocumentRequest] = []
        for original_index, meta, file in docs:
            file_content, file_name, content_type = file
            ext = file_name.rsplit(".", 1)[-1] if "." in file_name else "bin"
            r2_key = f"vehicles/{vehicle_id}/documents/{uuid4().hex}.{ext}"

            upload_items.append((r2_key, file_content, content_type))
            bulk_index_to_original_index.append(original_index)
            bulk_index_to_meta.append(meta)

        upload_result: BulkUploadResult[str] = await bulk_upload_to_r2(upload_items)

        created_with_original_index: list[tuple[int, VehicleDocument]] = []
        failed: list[BulkUploadFailure] = [BulkUploadFailure(index=bulk_index_to_original_index[bulk_idx], message=msg) for bulk_idx, msg in upload_result.failed]

        # Create records for successful uploads.
        for bulk_idx, r2_key in upload_result.succeeded:
            original_index = bulk_index_to_original_index[bulk_idx]
            meta = bulk_index_to_meta[bulk_idx]

            doc_data = meta.model_dump()
            doc_data["vehicle_id"] = vehicle_id
            doc_data["uploaded_by_id"] = ctx.user_id
            doc_data["file_path"] = r2_key

            try:
                document = await self._document_repo.create(doc_data)
            except Exception:
                await delete_from_r2(r2_key)
                failed.append(
                    BulkUploadFailure(
                        index=original_index,
                        message="File uploaded but could not be saved — please retry this document",
                    )
                )
                continue

            created_with_original_index.append((original_index, document))

            await self._log_audit(
                action="vehicle.document_uploaded",
                entity_type="vehicle_document",
                entity_id=document.id,
                user_id=ctx.user_id,
                user_role=ctx.user_role,
                new_value={
                    "vehicle_id": vehicle_id,
                    "document_type": meta.document_type.value,
                    "title": meta.title,
                },
                severity="NOTICE",
                category=AuditCategory.FLEET,
                event_type=AuditEventType.DOCUMENT_UPLOADED,
            )
            logger.info("vehicle.document_uploaded", vehicle_id=vehicle_id, doc_id=document.id)

        created_with_original_index.sort(key=lambda x: x[0])
        created = [doc for _idx, doc in created_with_original_index]

        if created:
            await self._sync_vehicle_compliance_cache(vehicle_id)

        return BulkDocumentUploadOutcome(created=created, failed=failed)

    async def get_documents(self, vehicle_id: str) -> list[VehicleDocument]:
        await self.get_vehicle(vehicle_id)
        return await self._document_repo.find_by_vehicle(vehicle_id)

    async def delete_document(self, vehicle_id: str, document_id: str, ctx: AuditContext) -> None:
        await self.get_vehicle(vehicle_id)
        doc = await self._document_repo.get_by_id(document_id)
        if doc is None or doc.vehicle_id != vehicle_id:
            raise NotFoundError(resource="document", id=document_id)

        if doc.file_path:
            await delete_from_r2(doc.file_path)

        await self._document_repo.hard_delete(document_id)
        await self._log_audit(
            action="vehicle.document_deleted",
            entity_type="vehicle_document",
            entity_id=document_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"vehicle_id": vehicle_id, "title": doc.title},
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.DOCUMENT_DELETED,
        )
        await self._sync_vehicle_compliance_cache(vehicle_id)

    # Vehicle Images

    async def add_images(
        self,
        vehicle_id: str,
        files: list[tuple[bytes, str, str]],
        ctx: AuditContext,
    ) -> BulkImageUploadOutcome:
        await self.get_vehicle(vehicle_id)

        upload_items = [(content, filename, {"vehicle_id": vehicle_id}) for content, filename, _ in files]
        result: BulkUploadResult[Any] = await bulk_upload_images(upload_items, raise_if_all_failed=False)

        urls: list[str] = []
        for _idx, cf_result in sorted(result.succeeded, key=lambda x: x[0]):
            image_data = {
                "vehicle_id": vehicle_id,
                "file_path": cf_result.id,
                "uploaded_by_id": ctx.user_id,
            }
            image = await self._image_repo.create(image_data)
            urls.append(generate_image_url(image.file_path))

            await self._log_audit(
                action="vehicle.image_uploaded",
                entity_type="vehicle_image",
                entity_id=image.id,
                user_id=ctx.user_id,
                user_role=ctx.user_role,
                new_value={"vehicle_id": vehicle_id},
                severity="NOTICE",
                category=AuditCategory.FLEET,
                event_type=AuditEventType.VEHICLE_UPDATED,
            )
            logger.info("vehicle.image_uploaded", vehicle_id=vehicle_id, image_id=image.id)

        failed = [BulkUploadFailure(index=idx, message=msg) for idx, msg in result.failed]
        return BulkImageUploadOutcome(urls=urls, failed=failed)

    async def get_images(self, vehicle_id: str) -> list[VehicleImage]:
        await self.get_vehicle(vehicle_id)
        return await self._image_repo.find_by_vehicle(vehicle_id)

    async def list_vehicle_image_urls(self, vehicle_id: str) -> list[str]:
        images = await self.get_images(vehicle_id)
        return [generate_image_url(img.file_path) for img in images]

    async def delete_image(self, vehicle_id: str, image_id: str, ctx: AuditContext) -> None:
        await self.get_vehicle(vehicle_id)
        image = await self._image_repo.get_by_id(image_id)
        if image is None or image.vehicle_id != vehicle_id:
            raise NotFoundError(resource="image", id=image_id)

        if image.file_path:
            await delete_image(image.file_path)

        await self._image_repo.hard_delete(image_id)

        await self._log_audit(
            action="vehicle.image_deleted",
            entity_type="vehicle_image",
            entity_id=image_id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"vehicle_id": vehicle_id},
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VEHICLE_UPDATED,
        )
        logger.info("vehicle.image_deleted", vehicle_id=vehicle_id, image_id=image_id)
