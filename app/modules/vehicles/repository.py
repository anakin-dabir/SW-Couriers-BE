from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import String, and_, case, cast, delete, desc, func, or_, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload, selectinload

from app.common.repository import BaseRepository
from app.modules.user.models import User
from app.modules.vehicles.enums import DefectStatus, DocumentType, MotFilterStatus, ScheduleEntrySource, TaxFilterStatus, VehicleAvailability, VehicleStatus
from app.modules.vehicles.models import (
    Vehicle,
    VehicleDefect,
    VehicleDefectImage,
    VehicleDeletionLog,
    VehicleDocument,
    VehicleDraft,
    VehicleImage,
    VehicleMaintenanceRecord,
    VehicleScheduleEntry,
    VehicleServiceRecord,
)


def _draft_vehicle_search_filter(search: str):
    q = search.strip()
    pattern = f"%{q}%"
    return or_(
        Vehicle.registration_number.ilike(pattern),
        Vehicle.fleet_number.ilike(pattern),
        Vehicle.make.ilike(pattern),
        Vehicle.model.ilike(pattern),
        cast(Vehicle.year, String).ilike(pattern),
        cast(Vehicle.vehicle_type, String).ilike(pattern),
    )


class VehicleRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Vehicle)

    async def get_by_id_with_images(self, vehicle_id: str) -> Vehicle | None:
        stmt = select(Vehicle).where(Vehicle.id == vehicle_id).options(selectinload(Vehicle.images), joinedload(Vehicle.preferred_driver))
        result = await self.session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def find_by_registration(self, registration_number: str) -> Vehicle | None:
        return await self.find_one(registration_number=registration_number)

    async def find_by_fleet_number(self, fleet_number: str) -> Vehicle | None:
        return await self.find_one(fleet_number=fleet_number)

    async def search(
        self,
        *,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        live_status: list[str] | None = None,
        availability: list[str] | None = None,
        mot_status: list[MotFilterStatus] | None = None,
        tax_status: list[TaxFilterStatus] | None = None,
    ) -> tuple[list[Vehicle], int]:
        draft_filter = Vehicle.status != VehicleStatus.DRAFT
        stmt = select(Vehicle).where(draft_filter)
        count_stmt = select(func.count()).select_from(Vehicle).where(draft_filter)

        if search:
            pattern = f"%{search.upper()}%"
            search_filter = Vehicle.registration_number.ilike(pattern)
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        if live_status:
            stmt = stmt.where(Vehicle.live_status.in_(live_status))
            count_stmt = count_stmt.where(Vehicle.live_status.in_(live_status))

        if availability:
            stmt = stmt.where(Vehicle.availability.in_(availability))
            count_stmt = count_stmt.where(Vehicle.availability.in_(availability))

        today = date.today()
        soon_end = today + timedelta(days=30)
        if mot_status:
            mot_filters = []
            if MotFilterStatus.VALID in mot_status:
                mot_filters.append(or_(Vehicle.mot_expiry > soon_end, Vehicle.mot_expiry.is_(None)))
            if MotFilterStatus.EXPIRING_SOON in mot_status:
                mot_filters.append(and_(Vehicle.mot_expiry >= today, Vehicle.mot_expiry <= soon_end))
            if MotFilterStatus.EXPIRED in mot_status:
                mot_filters.append(Vehicle.mot_expiry < today)
            if mot_filters:
                f = or_(*mot_filters)
                stmt = stmt.where(f)
                count_stmt = count_stmt.where(f)

        if tax_status:
            tax_filters = []
            if TaxFilterStatus.PAID in tax_status:
                tax_filters.append(or_(Vehicle.tax_due_date > soon_end, Vehicle.tax_due_date.is_(None)))
            if TaxFilterStatus.DUE_SOON in tax_status:
                tax_filters.append(and_(Vehicle.tax_due_date >= today, Vehicle.tax_due_date <= soon_end))
            if TaxFilterStatus.OVERDUE in tax_status:
                tax_filters.append(Vehicle.tax_due_date < today)
            if tax_filters:
                f = or_(*tax_filters)
                stmt = stmt.where(f)
                count_stmt = count_stmt.where(f)

        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        stmt = stmt.order_by(Vehicle.created_at.desc())
        stmt = stmt.offset((page - 1) * size).limit(size)

        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def search_with_defect_counts(
        self,
        *,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        live_status: list[str] | None = None,
        availability: list[str] | None = None,
        mot_status: list[MotFilterStatus] | None = None,
        tax_status: list[TaxFilterStatus] | None = None,
    ) -> tuple[list[tuple[Vehicle, int, int, int]], int]:
        defect_subq = (
            select(
                VehicleDefect.vehicle_id.label("vehicle_id"),
                func.count().label("total"),
                func.sum(case((VehicleDefect.status == DefectStatus.PENDING, 1), else_=0)).label("pending"),
                func.sum(case((VehicleDefect.status == DefectStatus.IN_PROGRESS, 1), else_=0)).label("in_progress"),
            )
            .where(VehicleDefect.status != DefectStatus.RESOLVED)
            .group_by(VehicleDefect.vehicle_id)
        ).subquery()

        draft_filter = Vehicle.status != VehicleStatus.DRAFT
        stmt = (
            select(
                Vehicle,
                func.coalesce(defect_subq.c.total, 0).label("defect_total"),
                func.coalesce(defect_subq.c.pending, 0).label("defect_pending"),
                func.coalesce(defect_subq.c.in_progress, 0).label("defect_in_progress"),
            )
            .select_from(Vehicle)
            .outerjoin(defect_subq, Vehicle.id == defect_subq.c.vehicle_id)
            .where(draft_filter)
        )
        count_stmt = select(func.count()).select_from(Vehicle).where(draft_filter)

        if search:
            pattern = f"%{search}%"
            search_filter = Vehicle.registration_number.ilike(pattern)
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        if live_status:
            stmt = stmt.where(Vehicle.live_status.in_(live_status))
            count_stmt = count_stmt.where(Vehicle.live_status.in_(live_status))

        if availability:
            stmt = stmt.where(Vehicle.availability.in_(availability))
            count_stmt = count_stmt.where(Vehicle.availability.in_(availability))

        today = date.today()
        soon_end = today + timedelta(days=30)
        if mot_status:
            mot_filters = []
            if MotFilterStatus.VALID in mot_status:
                mot_filters.append(or_(Vehicle.mot_expiry > soon_end, Vehicle.mot_expiry.is_(None)))
            if MotFilterStatus.EXPIRING_SOON in mot_status:
                mot_filters.append(and_(Vehicle.mot_expiry >= today, Vehicle.mot_expiry <= soon_end))
            if MotFilterStatus.EXPIRED in mot_status:
                mot_filters.append(Vehicle.mot_expiry < today)
            if mot_filters:
                f = or_(*mot_filters)
                stmt = stmt.where(f)
                count_stmt = count_stmt.where(f)

        if tax_status:
            tax_filters = []
            if TaxFilterStatus.PAID in tax_status:
                tax_filters.append(or_(Vehicle.tax_due_date > soon_end, Vehicle.tax_due_date.is_(None)))
            if TaxFilterStatus.DUE_SOON in tax_status:
                tax_filters.append(and_(Vehicle.tax_due_date >= today, Vehicle.tax_due_date <= soon_end))
            if TaxFilterStatus.OVERDUE in tax_status:
                tax_filters.append(Vehicle.tax_due_date < today)
            if tax_filters:
                f = or_(*tax_filters)
                stmt = stmt.where(f)
                count_stmt = count_stmt.where(f)

        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        stmt = stmt.options(selectinload(Vehicle.images))
        stmt = stmt.order_by(Vehicle.created_at.desc())
        stmt = stmt.offset((page - 1) * size).limit(size)

        result = await self.session.execute(stmt)
        rows = result.all()
        out = [(row[0], int(row[1]), int(row[2]), int(row[3])) for row in rows]
        return out, total

    async def get_fleet_stats(self) -> dict[str, int]:
        non_draft = select(func.count()).select_from(Vehicle).where(Vehicle.status != VehicleStatus.DRAFT)
        total_result = await self.session.execute(non_draft)
        total = total_result.scalar_one()
        active = await self.count(availability=VehicleAvailability.ACTIVE, status=VehicleStatus.ACTIVE)
        maintenance = await self.count(availability=VehicleAvailability.IN_MAINTENANCE)

        today = date.today()
        compliance_stmt = (
            select(func.count())
            .select_from(Vehicle)
            .where(
                Vehicle.status != VehicleStatus.DRAFT,
                or_(
                    Vehicle.mot_expiry < today,
                    Vehicle.tax_due_date < today,
                    Vehicle.insurance_expiry < today,
                ),
            )
        )
        compliance_result = await self.session.execute(compliance_stmt)
        compliance_alerts = compliance_result.scalar_one()

        return {
            "total_vehicles": total,
            "active_vehicles": active,
            "in_maintenance": maintenance,
            "compliance_alerts": compliance_alerts,
        }

    async def get_defect_counts_for_vehicles(self, vehicle_ids: list[str]) -> dict[str, dict[str, int]]:
        if not vehicle_ids:
            return {}

        stmt = (
            select(
                VehicleDefect.vehicle_id,
                VehicleDefect.status,
                func.count().label("cnt"),
            )
            .where(
                VehicleDefect.vehicle_id.in_(vehicle_ids),
                VehicleDefect.status != DefectStatus.RESOLVED,
            )
            .group_by(VehicleDefect.vehicle_id, VehicleDefect.status)
        )
        result = await self.session.execute(stmt)
        rows = result.all()

        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            vid = row[0]
            status_val = row[1]
            cnt = row[2]
            if vid not in counts:
                counts[vid] = {"total": 0, "pending": 0, "in_progress": 0}
            counts[vid]["total"] += cnt
            if status_val == DefectStatus.PENDING:
                counts[vid]["pending"] += cnt
            elif status_val == DefectStatus.IN_PROGRESS:
                counts[vid]["in_progress"] += cnt
        return counts


class VehicleDraftRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, VehicleDraft)

    async def find_by_vehicle_id(self, vehicle_id: str) -> VehicleDraft | None:
        return await self.find_one(vehicle_id=vehicle_id)

    async def find_by_draft_number(self, draft_number: str) -> VehicleDraft | None:
        return await self.find_one(draft_number=draft_number)

    async def get_by_id_with_vehicle_if_draft(self, draft_id: str) -> VehicleDraft | None:
        stmt = (
            select(VehicleDraft)
            .join(Vehicle, VehicleDraft.vehicle_id == Vehicle.id)
            .where(VehicleDraft.id == draft_id)
            .where(Vehicle.status == VehicleStatus.DRAFT)
            .options(joinedload(VehicleDraft.vehicle))
        )
        result = await self.session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def list_drafts(
        self,
        *,
        page: int = 1,
        size: int = 20,
        order_desc: bool = True,
        search: str | None = None,
    ) -> tuple[list[VehicleDraft], int]:
        order_cols = (VehicleDraft.created_at.desc(), VehicleDraft.id.desc()) if order_desc else (VehicleDraft.created_at.asc(), VehicleDraft.id.asc())
        stmt = (
            select(VehicleDraft)
            .join(Vehicle, VehicleDraft.vehicle_id == Vehicle.id)
            .where(Vehicle.status == VehicleStatus.DRAFT)
            .options(
                joinedload(VehicleDraft.vehicle).joinedload(Vehicle.preferred_driver),
            )
            .order_by(*order_cols)
        )
        count_stmt = select(func.count()).select_from(VehicleDraft).join(Vehicle, VehicleDraft.vehicle_id == Vehicle.id).where(Vehicle.status == VehicleStatus.DRAFT)
        if search and (q := search.strip()):
            sf = _draft_vehicle_search_filter(q)
            stmt = stmt.where(sf)
            count_stmt = count_stmt.where(sf)

        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        stmt = stmt.offset((page - 1) * size).limit(size)
        result = await self.session.execute(stmt)
        return list(result.unique().scalars().all()), total


class VehicleMaintenanceRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, VehicleMaintenanceRecord)

    async def find_by_vehicle(
        self,
        vehicle_id: str,
        *,
        page: int = 1,
        size: int = 20,
        maintenance_types: list[str] | None = None,
        search: str | None = None,
    ) -> tuple[list[VehicleMaintenanceRecord], int]:
        stmt = select(VehicleMaintenanceRecord).where(VehicleMaintenanceRecord.vehicle_id == vehicle_id)
        count_stmt = select(func.count()).select_from(VehicleMaintenanceRecord).where(VehicleMaintenanceRecord.vehicle_id == vehicle_id)

        if maintenance_types:
            vals = [str(m) for m in maintenance_types]
            overlap = VehicleMaintenanceRecord.maintenance_types.op("?|")(cast(vals, ARRAY(String)))
            stmt = stmt.where(overlap)
            count_stmt = count_stmt.where(overlap)

        if search and (q := search.strip()):
            pattern = f"%{q}%"
            search_filter = or_(
                VehicleMaintenanceRecord.reference.ilike(pattern),
                cast(VehicleMaintenanceRecord.garage, String).ilike(pattern),
                cast(VehicleMaintenanceRecord.maintenance_types, String).ilike(pattern),
            )
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        stmt = stmt.order_by(VehicleMaintenanceRecord.created_at.desc())
        stmt = stmt.offset((page - 1) * size).limit(size)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def find_latest_map_by_vehicle_ids(self, vehicle_ids: list[str]) -> dict[str, VehicleMaintenanceRecord]:
        if not vehicle_ids:
            return {}
        stmt = (
            select(VehicleMaintenanceRecord)
            .where(VehicleMaintenanceRecord.vehicle_id.in_(vehicle_ids))
            .order_by(VehicleMaintenanceRecord.created_at.desc())
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        out: dict[str, VehicleMaintenanceRecord] = {}
        for row in rows:
            if row.vehicle_id not in out:
                out[row.vehicle_id] = row
        return out

    async def find_latest_by_vehicle_id(self, vehicle_id: str) -> VehicleMaintenanceRecord | None:
        m = await self.find_latest_map_by_vehicle_ids([vehicle_id])
        return m.get(vehicle_id)

    async def get_cost_summary(self, vehicle_id: str) -> dict[str, Any]:
        stmt = (
            select(VehicleMaintenanceRecord.maintenance_types, VehicleMaintenanceRecord.cost)
            .where(VehicleMaintenanceRecord.vehicle_id == vehicle_id)
            .where(VehicleMaintenanceRecord.cost.isnot(None))
            .where(VehicleMaintenanceRecord.cost > 0)
        )
        result = await self.session.execute(stmt)
        rows = result.all()

        by_type: dict[str, float] = {}
        total_cost = 0.0
        for maintenance_types, cost in rows:
            if not cost or not maintenance_types:
                continue
            total_cost += float(cost)
            share = float(cost) / len(maintenance_types)
            for mt in maintenance_types:
                key = mt if isinstance(mt, str) else str(mt)
                by_type[key] = by_type.get(key, 0.0) + share

        by_type_list = [
            {"maintenance_type": k, "cost": round(v, 2), "percentage": round((v / total_cost * 100.0) if total_cost else 0.0, 1)}
            for k, v in sorted(by_type.items(), key=lambda x: -x[1])
        ]
        return {"vehicle_id": vehicle_id, "total_cost": round(total_cost, 2), "by_type": by_type_list}

    async def find_by_vehicle_and_date_range(
        self,
        vehicle_id: str,
        start_date: date,
        end_date: date,
    ) -> list[VehicleMaintenanceRecord]:
        stmt = (
            select(VehicleMaintenanceRecord)
            .where(VehicleMaintenanceRecord.vehicle_id == vehicle_id)
            .where(VehicleMaintenanceRecord.date_from <= end_date)
            .where(or_(VehicleMaintenanceRecord.date_to.is_(None), VehicleMaintenanceRecord.date_to >= start_date))
            .order_by(VehicleMaintenanceRecord.date_from)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class VehicleScheduleRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, VehicleScheduleEntry)

    async def find_by_vehicle_and_date_range(
        self,
        vehicle_id: str,
        start_date: date,
        end_date: date,
    ) -> list[VehicleScheduleEntry]:
        stmt = (
            select(VehicleScheduleEntry)
            .where(VehicleScheduleEntry.vehicle_id == vehicle_id)
            .where(VehicleScheduleEntry.date_from <= end_date)
            .where(or_(VehicleScheduleEntry.date_to.is_(None), VehicleScheduleEntry.date_to >= start_date))
            .order_by(VehicleScheduleEntry.date_from)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete_by_vehicle_source_and_source_id(
        self,
        vehicle_id: str,
        source: ScheduleEntrySource,
        source_id: str,
    ) -> int:
        stmt = (
            delete(VehicleScheduleEntry)
            .where(VehicleScheduleEntry.vehicle_id == vehicle_id)
            .where(VehicleScheduleEntry.source == source)
            .where(VehicleScheduleEntry.source_id == source_id)
        )
        result = await self.session.execute(stmt)
        return getattr(result, "rowcount", 0) or 0

    async def delete_by_vehicle_and_source_and_date_range(
        self,
        vehicle_id: str,
        source: ScheduleEntrySource,
        start_date: date,
        end_date: date,
    ) -> int:
        stmt = (
            delete(VehicleScheduleEntry)
            .where(VehicleScheduleEntry.vehicle_id == vehicle_id)
            .where(VehicleScheduleEntry.source == source)
            .where(VehicleScheduleEntry.date_from <= end_date)
            .where(or_(VehicleScheduleEntry.date_to.is_(None), VehicleScheduleEntry.date_to >= start_date))
        )
        result = await self.session.execute(stmt)
        return getattr(result, "rowcount", 0) or 0

    async def close_availability_ranges(self, vehicle_id: str, close_date: date) -> int:
        """Close all open AVAILABILITY rows: set date_to = close_date if date_from <= close_date, else date_to = date_from (cancel future open). See SCHEDULE_SPEC.md."""
        from sqlalchemy import case

        stmt = (
            update(VehicleScheduleEntry)
            .where(VehicleScheduleEntry.vehicle_id == vehicle_id)
            .where(VehicleScheduleEntry.source == ScheduleEntrySource.AVAILABILITY)
            .where(VehicleScheduleEntry.date_to.is_(None))
            .values(date_to=case((VehicleScheduleEntry.date_from <= close_date, close_date), else_=VehicleScheduleEntry.date_from))
        )
        result = await self.session.execute(stmt)
        return getattr(result, "rowcount", 0) or 0


def _defect_list_search_filter(search: str, reporter: Any, defect_model: Any) -> Any:
    q = search.strip()
    pattern = f"%{q}%"
    full_name = func.trim(
        func.concat(
            func.coalesce(cast(reporter.first_name, String), ""),
            " ",
            func.coalesce(cast(reporter.last_name, String), ""),
        )
    )
    return or_(
        cast(reporter.first_name, String).ilike(pattern),
        cast(reporter.last_name, String).ilike(pattern),
        full_name.ilike(pattern),
        cast(defect_model.route_id, String).ilike(pattern),
        defect_model.reference.ilike(pattern),
    )


class VehicleDefectRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, VehicleDefect)

    async def get_by_id_with_images(self, defect_id: str) -> VehicleDefect | None:
        stmt = select(VehicleDefect).where(VehicleDefect.id == defect_id).options(selectinload(VehicleDefect.images), selectinload(VehicleDefect.reported_by))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_vehicle(
        self,
        vehicle_id: str,
        *,
        page: int = 1,
        size: int = 20,
        statuses: list[DefectStatus] | None = None,
        search: str | None = None,
    ) -> tuple[list[VehicleDefect], int]:
        stmt = select(VehicleDefect).where(VehicleDefect.vehicle_id == vehicle_id)
        count_stmt = select(func.count()).select_from(VehicleDefect).where(VehicleDefect.vehicle_id == vehicle_id)

        if statuses:
            stmt = stmt.where(VehicleDefect.status.in_(statuses))
            count_stmt = count_stmt.where(VehicleDefect.status.in_(statuses))

        if search:
            reporter = aliased(User)
            sf = _defect_list_search_filter(search, reporter, VehicleDefect)
            stmt = stmt.outerjoin(reporter, VehicleDefect.reported_by_id == reporter.id).where(sf)
            count_stmt = count_stmt.outerjoin(reporter, VehicleDefect.reported_by_id == reporter.id).where(sf)

        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        stmt = stmt.order_by(VehicleDefect.reported_at.desc())
        stmt = stmt.offset((page - 1) * size).limit(size)

        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def find_by_vehicle_with_images(
        self,
        vehicle_id: str,
        *,
        page: int = 1,
        size: int = 20,
        statuses: list[DefectStatus] | None = None,
        search: str | None = None,
    ) -> tuple[list[VehicleDefect], int]:
        stmt = select(VehicleDefect).where(VehicleDefect.vehicle_id == vehicle_id)
        count_stmt = select(func.count()).select_from(VehicleDefect).where(VehicleDefect.vehicle_id == vehicle_id)

        if statuses:
            stmt = stmt.where(VehicleDefect.status.in_(statuses))
            count_stmt = count_stmt.where(VehicleDefect.status.in_(statuses))

        if search:
            reporter = aliased(User)
            sf = _defect_list_search_filter(search, reporter, VehicleDefect)
            stmt = stmt.outerjoin(reporter, VehicleDefect.reported_by_id == reporter.id).where(sf)
            count_stmt = count_stmt.outerjoin(reporter, VehicleDefect.reported_by_id == reporter.id).where(sf)

        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        stmt = stmt.options(selectinload(VehicleDefect.images), selectinload(VehicleDefect.reported_by))
        stmt = stmt.order_by(VehicleDefect.reported_at.desc())
        stmt = stmt.offset((page - 1) * size).limit(size)

        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def count_open_by_vehicle(self, vehicle_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(VehicleDefect)
            .where(
                VehicleDefect.vehicle_id == vehicle_id,
                VehicleDefect.status != DefectStatus.RESOLVED,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()


class VehicleServiceRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, VehicleServiceRecord)

    async def latest_mileage_at_service_for_vehicle(self, vehicle_id: str) -> int | None:
        stmt = (
            select(VehicleServiceRecord.mileage_at_service)
            .where(
                VehicleServiceRecord.vehicle_id == vehicle_id,
                VehicleServiceRecord.mileage_at_service.isnot(None),
            )
            .order_by(desc(VehicleServiceRecord.service_date), desc(VehicleServiceRecord.created_at))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def latest_next_service_due_for_vehicle(self, vehicle_id: str) -> date | None:
        stmt = (
            select(VehicleServiceRecord.next_service_due)
            .where(VehicleServiceRecord.vehicle_id == vehicle_id)
            .order_by(desc(VehicleServiceRecord.service_date), desc(VehicleServiceRecord.created_at))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_vehicle(
        self,
        vehicle_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[VehicleServiceRecord], int]:
        return await self.find_all(
            page=page,
            size=size,
            order_by="service_date",
            order_desc=True,
            vehicle_id=vehicle_id,
        )


class VehicleDeletionRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, VehicleDeletionLog)

    async def list_paginated(
        self,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[VehicleDeletionLog], int]:
        stmt = select(VehicleDeletionLog).options(selectinload(VehicleDeletionLog.deleted_by)).order_by(desc(VehicleDeletionLog.created_at), desc(VehicleDeletionLog.id))
        count_stmt = select(func.count()).select_from(VehicleDeletionLog)
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()
        stmt = stmt.offset((page - 1) * size).limit(size)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total


class VehicleDocumentRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, VehicleDocument)

    async def find_by_vehicle(self, vehicle_id: str) -> list[VehicleDocument]:
        stmt = select(VehicleDocument).where(VehicleDocument.vehicle_id == vehicle_id).order_by(VehicleDocument.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_by_document_types(
        self,
        vehicle_id: str,
        types: tuple[DocumentType, ...],
    ) -> dict[DocumentType, VehicleDocument | None]:
        if not types:
            return {}

        stmt = select(VehicleDocument).where(
            VehicleDocument.vehicle_id == vehicle_id,
            VehicleDocument.document_type.in_(types),
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())

        def _sort_key(d: VehicleDocument) -> tuple[date, datetime]:
            return (d.expiry_date or date.min, d.created_at)

        best: dict[DocumentType, VehicleDocument | None] = dict.fromkeys(types)
        for doc in rows:
            t = doc.document_type
            if t not in best:
                continue
            cur = best[t]
            if cur is None or _sort_key(doc) > _sort_key(cur):
                best[t] = doc
        return best

    async def count_by_vehicle(self, vehicle_id: str) -> int:
        stmt = select(func.count()).select_from(VehicleDocument).where(VehicleDocument.vehicle_id == vehicle_id)
        result = await self.session.execute(stmt)
        return result.scalar_one()


class VehicleImageRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, VehicleImage)

    async def find_by_vehicle(self, vehicle_id: str) -> list[VehicleImage]:
        stmt = select(VehicleImage).where(VehicleImage.vehicle_id == vehicle_id).order_by(VehicleImage.created_at.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_vehicle(self, vehicle_id: str) -> int:
        stmt = select(func.count()).select_from(VehicleImage).where(VehicleImage.vehicle_id == vehicle_id)
        result = await self.session.execute(stmt)
        return result.scalar_one()


class VehicleDefectImageRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, VehicleDefectImage)

    async def find_by_defect(self, defect_id: str) -> list[VehicleDefectImage]:
        stmt = select(VehicleDefectImage).where(VehicleDefectImage.defect_id == defect_id).order_by(VehicleDefectImage.created_at.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
