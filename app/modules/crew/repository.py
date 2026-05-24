from __future__ import annotations

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import UserRole, UserStatus
from app.common.repository import BaseRepository
from app.modules.crew.models import Crew, RouteCrewAssignment
from app.modules.planning.enums import RouteStatus
from app.modules.planning.models import Route
from app.modules.user.models import User
from app.modules.vehicles.enums import VehicleAvailability, VehicleStatus
from app.modules.vehicles.models import Vehicle


class CrewRepository(BaseRepository):
    """Data access for ``crews``. ``driver_id`` references ``users.id``."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Crew)

    async def find_open_for_driver(self, driver_id: str) -> Crew | None:
        stmt = select(Crew).where(Crew.driver_id == driver_id, Crew.ended_at.is_(None)).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_open_for_vehicle(self, vehicle_id: str) -> Crew | None:
        stmt = select(Crew).where(Crew.vehicle_id == vehicle_id, Crew.ended_at.is_(None)).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def lock_open_for_driver(self, driver_id: str) -> Crew | None:
        """SELECT ... FOR UPDATE on the open crew for a driver, if any."""
        stmt = (
            select(Crew)
            .where(Crew.driver_id == driver_id, Crew.ended_at.is_(None))
            .with_for_update()
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def lock_open_for_vehicle(self, vehicle_id: str) -> Crew | None:
        """SELECT ... FOR UPDATE on the open crew for a vehicle, if any."""
        stmt = (
            select(Crew)
            .where(Crew.vehicle_id == vehicle_id, Crew.ended_at.is_(None))
            .with_for_update()
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def lock_by_id(self, crew_id: str) -> Crew | None:
        """SELECT ... FOR UPDATE by crew id."""
        stmt = select(Crew).where(Crew.id == crew_id).with_for_update().limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_history_for_driver(
        self,
        driver_id: str,
        *,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[Crew], int]:
        base = select(Crew).where(Crew.driver_id == driver_id)
        count_stmt = select(func.count()).select_from(Crew).where(Crew.driver_id == driver_id)
        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = base.order_by(Crew.started_at.desc()).offset((page - 1) * size).limit(size)
        return list((await self.session.execute(stmt)).scalars().all()), total

    async def list_history_for_vehicle(
        self,
        vehicle_id: str,
        *,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[Crew], int]:
        base = select(Crew).where(Crew.vehicle_id == vehicle_id)
        count_stmt = select(func.count()).select_from(Crew).where(Crew.vehicle_id == vehicle_id)
        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = base.order_by(Crew.started_at.desc()).offset((page - 1) * size).limit(size)
        return list((await self.session.execute(stmt)).scalars().all()), total

    async def list_active_drivers_without_crew(
        self,
        *,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
    ) -> tuple[list[User], int]:
        """Users with DRIVER role + ACTIVE status that have no currently open crew."""
        no_open_crew = ~exists().where(Crew.driver_id == User.id, Crew.ended_at.is_(None))
        conditions = [
            User.status == UserStatus.ACTIVE.value,
            User.role == UserRole.DRIVER.value,
            no_open_crew,
        ]
        if search:
            term = f"%{search.strip()}%"
            conditions.append(
                or_(
                    User.email.ilike(term),
                    User.first_name.ilike(term),
                    User.last_name.ilike(term),
                )
            )

        where_clause = and_(*conditions)
        count_stmt = select(func.count()).select_from(User).where(where_clause)
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(User)
            .where(where_clause)
            .order_by(User.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        return list((await self.session.execute(stmt)).scalars().all()), total

    async def list_active_vehicles_without_crew(
        self,
        *,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
    ) -> tuple[list[Vehicle], int]:
        """Active + available vehicles with no currently open crew."""
        no_open_crew = ~exists().where(Crew.vehicle_id == Vehicle.id, Crew.ended_at.is_(None))
        conditions = [
            Vehicle.status == VehicleStatus.ACTIVE.value,
            Vehicle.availability == VehicleAvailability.ACTIVE.value,
            no_open_crew,
        ]
        if search:
            term = f"%{search.strip()}%"
            conditions.append(or_(Vehicle.fleet_number.ilike(term), Vehicle.registration_number.ilike(term)))

        where_clause = and_(*conditions)
        count_stmt = select(func.count()).select_from(Vehicle).where(where_clause)
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(Vehicle)
            .where(where_clause)
            .order_by(Vehicle.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        return list((await self.session.execute(stmt)).scalars().all()), total

    async def list_active_vehicles_without_open_route(
        self,
        *,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
    ) -> tuple[list[Vehicle], int]:
        """Active vehicles that are not currently on an open route (paired or not)."""
        on_open_route = exists().where(
            and_(
                Crew.vehicle_id == Vehicle.id,
                Crew.ended_at.is_(None),
                exists().where(
                    and_(
                        RouteCrewAssignment.crew_id == Crew.id,
                        RouteCrewAssignment.unassigned_at.is_(None),
                    )
                ).correlate(Crew),
            )
        )
        conditions = [
            Vehicle.status == VehicleStatus.ACTIVE.value,
            Vehicle.availability == VehicleAvailability.ACTIVE.value,
            ~on_open_route,
        ]
        if search:
            term = f"%{search.strip()}%"
            conditions.append(or_(Vehicle.fleet_number.ilike(term), Vehicle.registration_number.ilike(term)))

        where_clause = and_(*conditions)
        count_stmt = select(func.count()).select_from(Vehicle).where(where_clause)
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(Vehicle)
            .where(where_clause)
            .order_by(Vehicle.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        return list((await self.session.execute(stmt)).scalars().all()), total


class RouteCrewAssignmentRepository(BaseRepository):
    """Data access for ``route_crew_assignments``."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, RouteCrewAssignment)

    async def find_open_for_route(self, route_id: str) -> RouteCrewAssignment | None:
        stmt = (
            select(RouteCrewAssignment)
            .where(
                RouteCrewAssignment.route_id == route_id,
                RouteCrewAssignment.unassigned_at.is_(None),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_open_for_crew(self, crew_id: str) -> RouteCrewAssignment | None:
        stmt = (
            select(RouteCrewAssignment)
            .where(
                RouteCrewAssignment.crew_id == crew_id,
                RouteCrewAssignment.unassigned_at.is_(None),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def lock_open_for_route(self, route_id: str) -> RouteCrewAssignment | None:
        """SELECT ... FOR UPDATE on the open assignment for a route, if any."""
        stmt = (
            select(RouteCrewAssignment)
            .where(
                RouteCrewAssignment.route_id == route_id,
                RouteCrewAssignment.unassigned_at.is_(None),
            )
            .with_for_update()
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def lock_open_for_crew(self, crew_id: str) -> RouteCrewAssignment | None:
        """SELECT ... FOR UPDATE on the open assignment for a crew, if any."""
        stmt = (
            select(RouteCrewAssignment)
            .where(
                RouteCrewAssignment.crew_id == crew_id,
                RouteCrewAssignment.unassigned_at.is_(None),
            )
            .with_for_update()
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_history_for_route(self, route_id: str) -> list[RouteCrewAssignment]:
        stmt = (
            select(RouteCrewAssignment)
            .where(RouteCrewAssignment.route_id == route_id)
            .order_by(RouteCrewAssignment.assigned_at.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_history_for_crew(
        self,
        crew_id: str,
        *,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[RouteCrewAssignment], int]:
        base = select(RouteCrewAssignment).where(RouteCrewAssignment.crew_id == crew_id)
        count_stmt = (
            select(func.count())
            .select_from(RouteCrewAssignment)
            .where(RouteCrewAssignment.crew_id == crew_id)
        )
        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = (
            base.order_by(RouteCrewAssignment.assigned_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        return list((await self.session.execute(stmt)).scalars().all()), total

    async def list_assignable_routes(
        self,
        *,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
    ) -> tuple[list[Route], int]:
        """Non-completed routes that have no currently open crew assignment.

        Status filter excludes ``COMPLETED`` (terminal). Routes in ``DRAFT`` /
        ``ASSIGNED`` / ``ACTIVE`` with no open ``RouteCrewAssignment`` row are
        returned. Search matches ``route_code``.
        """
        no_open_assignment = ~exists().where(
            RouteCrewAssignment.route_id == Route.id,
            RouteCrewAssignment.unassigned_at.is_(None),
        )
        conditions = [
            Route.status != RouteStatus.COMPLETED.value,
            no_open_assignment,
        ]
        if search:
            term = f"%{search.strip()}%"
            conditions.append(Route.route_code.ilike(term))

        where_clause = and_(*conditions)
        count_stmt = select(func.count()).select_from(Route).where(where_clause)
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(Route)
            .where(where_clause)
            .order_by(Route.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        return list((await self.session.execute(stmt)).scalars().all()), total
