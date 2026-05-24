from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.common.repository import BaseRepository
from app.modules.vehicle_inspections.enums import InspectionStatus
from app.modules.vehicle_inspections.models import VehicleInspection


class InspectionRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, VehicleInspection)

    async def get_by_id_with_vehicle_and_driver(self, inspection_id: str) -> VehicleInspection | None:
        stmt = (
            select(VehicleInspection)
            .where(VehicleInspection.id == inspection_id)
            .options(
                joinedload(VehicleInspection.vehicle),
                joinedload(VehicleInspection.driver),
            )
        )
        result = await self.session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def find_latest_by_driver_and_vehicle_and_status(
        self,
        driver_id: str,
        vehicle_id: str,
        status: InspectionStatus,
    ) -> VehicleInspection | None:
        stmt = (
            select(VehicleInspection)
            .where(
                VehicleInspection.driver_id == driver_id,
                VehicleInspection.vehicle_id == vehicle_id,
                VehicleInspection.status == status,
            )
            .order_by(VehicleInspection.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def find_latest_by_driver_not_in_progress(self, driver_id: str) -> VehicleInspection | None:
        stmt = (
            select(VehicleInspection)
            .where(
                VehicleInspection.driver_id == driver_id,
                VehicleInspection.status != InspectionStatus.IN_PROGRESS,
            )
            .order_by(VehicleInspection.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.unique().scalar_one_or_none()
