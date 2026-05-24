"""Planning data access helpers for stop POD flow."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.common.repository import BaseRepository
from app.modules.drivers.models import Driver
from app.modules.planning.models import Route, RoutePlan, RouteStop, StopPod, StopPodPhoto


class StopExecutionRepository(BaseRepository):
    """Repository for route-stop and POD related execution writes."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, RouteStop)

    async def get_route_stop(self, *, route_id: str, stop_id: str) -> RouteStop | None:
        stmt = select(RouteStop).where(RouteStop.id == stop_id, RouteStop.route_id == route_id)
        return (await self.session.execute(stmt)).scalars().first()

    async def get_or_create_stop_pod(self, delivery_stop_id: str) -> StopPod:
        stmt = select(StopPod).where(StopPod.delivery_stop_id == delivery_stop_id)
        row = (await self.session.execute(stmt)).scalars().first()
        if row is not None:
            return row
        row = StopPod(delivery_stop_id=delivery_stop_id)
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def list_pod_photos(self, delivery_stop_id: str) -> list[StopPodPhoto]:
        stmt = (
            select(StopPodPhoto)
            .where(StopPodPhoto.delivery_stop_id == delivery_stop_id)
            .order_by(StopPodPhoto.sort_order.asc(), StopPodPhoto.created_at.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create_pod_photo(
        self,
        *,
        delivery_stop_id: str,
        image_key: str,
        sort_order: int,
        uploaded_by_driver_id: str,
    ) -> StopPodPhoto:
        row = StopPodPhoto(
            delivery_stop_id=delivery_stop_id,
            image_key=image_key,
            sort_order=sort_order,
            uploaded_by_driver_id=uploaded_by_driver_id,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def delete_pod_photo(self, *, delivery_stop_id: str, photo_id: str) -> bool:
        stmt = select(StopPodPhoto).where(StopPodPhoto.id == photo_id, StopPodPhoto.delivery_stop_id == delivery_stop_id)
        row = (await self.session.execute(stmt)).scalars().first()
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True


class RouteCalendarRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_routes_with_service_date(
        self,
        vehicle_id: str,
        start_date: date,
        end_date: date,
    ) -> list[tuple[Route, date]]:
        stmt = (
            select(Route, RoutePlan.service_date)
            .join(RoutePlan, Route.plan_id == RoutePlan.id)
            .where(Route.vehicle_id == vehicle_id)
            .where(RoutePlan.service_date >= start_date)
            .where(RoutePlan.service_date <= end_date)
            .options(joinedload(Route.driver).joinedload(Driver.user))
            .order_by(RoutePlan.service_date.asc(), Route.route_code.asc())
        )
        result = await self.session.execute(stmt)
        return [(route, svc_date) for route, svc_date in result.unique().all()]
