# pyright: reportAttributeAccessIssue=false
# CursorResult.rowcount exists at runtime but is missing from SQLAlchemy's type stubs.

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from fastapi import Request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ConflictError, NotFoundError
from app.common.service import BaseService
from app.common.types import AuditContext
from app.common.utils import get_client_ip
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.crew.enums import CrewEndReason, RouteCrewUnassignReason
from app.modules.crew.models import Crew, RouteCrewAssignment
from app.modules.crew.repository import CrewRepository, RouteCrewAssignmentRepository
from app.modules.crew.types import (
    CascadeCloseOutcome,
    CrewReassignmentOutcome,
    RouteCrewSwapOutcome,
)
from app.modules.planning.models import Route
from app.modules.user.models import User
from app.modules.vehicles.models import Vehicle

logger = structlog.get_logger()


class CrewService(BaseService):
    """Crew + route-crew-assignment lifecycle. Designed to be called from other services.

    ``driver_id`` in this module always means a ``users.id`` of a user with the
    DRIVER role. We keep the column name for semantic clarity.
    """

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._crew_repo = CrewRepository(session)
        self._rca_repo = RouteCrewAssignmentRepository(session)
        self._audit = AuditService(session, request)
        self._ip_address = get_client_ip(request) if request else None
        self._user_agent = request.headers.get("user-agent") if request else None

    async def get_crew(self, crew_id: str) -> Crew:
        crew = await self._crew_repo.get_by_id(crew_id)
        if crew is None:
            raise NotFoundError(resource="crew", id=crew_id)
        return crew

    async def get_open_crew_for_driver(self, driver_id: str) -> Crew | None:
        return await self._crew_repo.find_open_for_driver(driver_id)

    async def get_open_crew_for_vehicle(self, vehicle_id: str) -> Crew | None:
        return await self._crew_repo.find_open_for_vehicle(vehicle_id)

    async def get_current_assignment_for_route(self, route_id: str) -> RouteCrewAssignment | None:
        return await self._rca_repo.find_open_for_route(route_id)

    async def get_current_crew_for_route(self, route_id: str) -> Crew | None:
        assn = await self._rca_repo.find_open_for_route(route_id)
        if assn is None:
            return None
        return await self._crew_repo.get_by_id(assn.crew_id)

    async def list_active_drivers_without_crew(
        self,
        *,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
    ) -> tuple[list[User], int]:
        """Driver-role users with ACTIVE status and no open crew."""
        return await self._crew_repo.list_active_drivers_without_crew(page=page, size=size, search=search)

    async def list_active_vehicles_without_crew(
        self,
        *,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
    ) -> tuple[list[Vehicle], int]:
        """Active+available vehicles with no open crew."""
        return await self._crew_repo.list_active_vehicles_without_crew(page=page, size=size, search=search)

    async def list_active_vehicles_without_open_route(
        self,
        *,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
    ) -> tuple[list[Vehicle], int]:
        """Active vehicles not on an open route."""
        return await self._crew_repo.list_active_vehicles_without_open_route(page=page, size=size, search=search)

    async def list_crew_history_for_driver(
        self,
        driver_id: str,
        *,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[Crew], int]:
        return await self._crew_repo.list_history_for_driver(driver_id, page=page, size=size)

    async def list_crew_history_for_vehicle(
        self,
        vehicle_id: str,
        *,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[Crew], int]:
        return await self._crew_repo.list_history_for_vehicle(vehicle_id, page=page, size=size)

    async def list_assignable_routes(
        self,
        *,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
    ) -> tuple[list[Route], int]:
        """Non-completed routes with no currently open crew assignment."""
        return await self._rca_repo.list_assignable_routes(page=page, size=size, search=search)

    async def list_assignment_history_for_route(self, route_id: str) -> list[RouteCrewAssignment]:
        return await self._rca_repo.list_history_for_route(route_id)

    async def list_assignment_history_for_crew(
        self,
        crew_id: str,
        *,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[RouteCrewAssignment], int]:
        return await self._rca_repo.list_history_for_crew(crew_id, page=page, size=size)

    async def open_crew(
        self,
        *,
        driver_id: str,
        vehicle_id: str,
        ctx: AuditContext,
        notes: str | None = None,
    ) -> Crew:
        """Open a new crew (driver user + vehicle). 409 if either side already has an open crew."""
        crew = Crew(
            driver_id=driver_id,
            vehicle_id=vehicle_id,
            started_by_id=ctx.user_id,
            notes=notes,
        )
        self._session.add(crew)
        try:
            await self._session.flush()
        except IntegrityError as e:
            raise ConflictError("Driver or vehicle is already paired in an open crew.") from e

        await self._audit.log(
            action="crew.opened",
            entity_type="crew",
            entity_id=crew.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={
                "driver_id": driver_id,
                "vehicle_id": vehicle_id,
                "started_at": crew.started_at.isoformat() if isinstance(crew.started_at, datetime) else None,
            },
            ip_address=ctx.ip_address or self._ip_address,
            user_agent=ctx.user_agent or self._user_agent,
            category=AuditCategory.FLEET,
            event_type=AuditEventType.CREW_OPENED,
        )
        logger.info("crew.opened", crew_id=crew.id, driver_id=driver_id, vehicle_id=vehicle_id)
        return crew

    async def close_crew(
        self,
        crew_id: str,
        *,
        reason: CrewEndReason,
        ctx: AuditContext,
        force: bool = False,
    ) -> Crew:
        """Close a crew. 409 if it has an open route assignment unless ``force=True``."""
        crew = await self._crew_repo.lock_by_id(crew_id)
        if crew is None:
            raise NotFoundError(resource="crew", id=crew_id)
        if crew.ended_at is not None:
            raise ConflictError("Crew is already closed.")

        open_assn = await self._rca_repo.lock_open_for_crew(crew_id)
        if open_assn is not None:
            if not force:
                raise ConflictError(
                    "Crew has an active route assignment. Complete or cancel the route first, or close with force=true."
                )
            await self._close_assignment(open_assn, reason=RouteCrewUnassignReason.MANUAL, ctx=ctx)

        await self._close_crew_row(crew, reason=reason, ctx=ctx)
        return crew

    async def reassign_vehicle_for_driver(
        self,
        *,
        driver_id: str,
        new_vehicle_id: str,
        ctx: AuditContext,
        reason: CrewEndReason = CrewEndReason.VEHICLE_SWAP,
        notes: str | None = None,
    ) -> CrewReassignmentOutcome:
        """Close the driver's open crew and open a new one with a different vehicle."""
        previous = await self._crew_repo.lock_open_for_driver(driver_id)
        if previous is None:
            raise NotFoundError(resource="open crew for driver", id=driver_id)

        open_assn = await self._rca_repo.lock_open_for_crew(previous.id)
        if open_assn is not None:
            raise ConflictError("Driver is on an active route. Complete or cancel the route first.")

        await self._close_crew_row(previous, reason=reason, ctx=ctx)
        new_crew = await self.open_crew(
            driver_id=driver_id,
            vehicle_id=new_vehicle_id,
            ctx=ctx,
            notes=notes,
        )
        return CrewReassignmentOutcome(previous_crew=previous, new_crew=new_crew)

    async def reassign_driver_for_vehicle(
        self,
        *,
        vehicle_id: str,
        new_driver_id: str,
        ctx: AuditContext,
        reason: CrewEndReason = CrewEndReason.DRIVER_HANDOVER,
        notes: str | None = None,
    ) -> CrewReassignmentOutcome:
        """Close the vehicle's open crew and open a new one with a different driver (handover)."""
        previous = await self._crew_repo.lock_open_for_vehicle(vehicle_id)
        if previous is None:
            raise NotFoundError(resource="open crew for vehicle", id=vehicle_id)

        open_assn = await self._rca_repo.lock_open_for_crew(previous.id)
        if open_assn is not None:
            raise ConflictError("Vehicle is on an active route. Complete or cancel the route first.")

        await self._close_crew_row(previous, reason=reason, ctx=ctx)
        new_crew = await self.open_crew(
            driver_id=new_driver_id,
            vehicle_id=vehicle_id,
            ctx=ctx,
            notes=notes,
        )
        return CrewReassignmentOutcome(previous_crew=previous, new_crew=new_crew)

    async def assign_route(
        self,
        *,
        route_id: str,
        crew_id: str,
        ctx: AuditContext,
    ) -> RouteCrewAssignment:
        """Assign a crew to a route. 409 if route or crew already has an open assignment."""
        crew = await self._crew_repo.lock_by_id(crew_id)
        if crew is None:
            raise NotFoundError(resource="crew", id=crew_id)
        if crew.ended_at is not None:
            raise ConflictError("Crew is closed; cannot assign a route to it.")

        existing = await self._rca_repo.lock_open_for_route(route_id)
        if existing is not None:
            raise ConflictError("Route already has an open crew assignment.")

        assn = RouteCrewAssignment(
            route_id=route_id,
            crew_id=crew_id,
            assigned_by_id=ctx.user_id,
        )
        self._session.add(assn)
        try:
            await self._session.flush()
        except IntegrityError as e:
            raise ConflictError("Route or crew already has an open assignment.") from e

        await self._audit.log(
            action="route_crew.assigned",
            entity_type="route_crew_assignment",
            entity_id=assn.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            new_value={"route_id": route_id, "crew_id": crew_id},
            ip_address=ctx.ip_address or self._ip_address,
            user_agent=ctx.user_agent or self._user_agent,
            category=AuditCategory.FLEET,
            event_type=AuditEventType.ROUTE_CREW_ASSIGNED,
        )
        logger.info("route_crew.assigned", route_id=route_id, crew_id=crew_id)
        return assn

    async def unassign_route(
        self,
        *,
        route_id: str,
        reason: RouteCrewUnassignReason,
        ctx: AuditContext,
    ) -> RouteCrewAssignment:
        """Close the open route-crew assignment for a route."""
        assn = await self._rca_repo.lock_open_for_route(route_id)
        if assn is None:
            raise NotFoundError(resource="open route assignment for route", id=route_id)
        return await self._close_assignment(assn, reason=reason, ctx=ctx)

    async def swap_route_crew(
        self,
        *,
        route_id: str,
        new_crew_id: str,
        ctx: AuditContext,
        reason: RouteCrewUnassignReason = RouteCrewUnassignReason.CREW_SWAP,
    ) -> RouteCrewSwapOutcome:
        """Atomically swap the open crew on a route to a new crew."""
        previous = await self._rca_repo.lock_open_for_route(route_id)
        if previous is None:
            raise NotFoundError(resource="open route assignment for route", id=route_id)

        new_crew = await self._crew_repo.lock_by_id(new_crew_id)
        if new_crew is None:
            raise NotFoundError(resource="crew", id=new_crew_id)
        if new_crew.ended_at is not None:
            raise ConflictError("New crew is closed; cannot assign.")

        await self._close_assignment(previous, reason=reason, ctx=ctx)
        new_assn = await self.assign_route(route_id=route_id, crew_id=new_crew_id, ctx=ctx)
        return RouteCrewSwapOutcome(previous_assignment=previous, new_assignment=new_assn)

    async def force_close_for_driver(
        self,
        driver_id: str,
        *,
        ctx: AuditContext,
        reason: CrewEndReason = CrewEndReason.DRIVER_SUSPENDED,
    ) -> CascadeCloseOutcome:
        """Cascade-close any open crew (and its route assignment) for a driver. Idempotent."""
        crew = await self._crew_repo.lock_open_for_driver(driver_id)
        if crew is None:
            return CascadeCloseOutcome(crew=None, route_assignment=None)

        assignment_reason = (
            RouteCrewUnassignReason.DRIVER_SUSPENDED
            if reason == CrewEndReason.DRIVER_SUSPENDED
            else RouteCrewUnassignReason.MANUAL
        )
        assn = await self._rca_repo.lock_open_for_crew(crew.id)
        if assn is not None:
            await self._close_assignment(assn, reason=assignment_reason, ctx=ctx)

        await self._close_crew_row(crew, reason=reason, ctx=ctx)
        return CascadeCloseOutcome(crew=crew, route_assignment=assn)

    async def force_close_for_vehicle(
        self,
        vehicle_id: str,
        *,
        ctx: AuditContext,
        reason: CrewEndReason = CrewEndReason.VEHICLE_OUT_OF_SERVICE,
    ) -> CascadeCloseOutcome:
        """Cascade-close any open crew (and its route assignment) for a vehicle. Idempotent."""
        crew = await self._crew_repo.lock_open_for_vehicle(vehicle_id)
        if crew is None:
            return CascadeCloseOutcome(crew=None, route_assignment=None)

        assignment_reason = (
            RouteCrewUnassignReason.VEHICLE_OUT_OF_SERVICE
            if reason == CrewEndReason.VEHICLE_OUT_OF_SERVICE
            else RouteCrewUnassignReason.MANUAL
        )
        assn = await self._rca_repo.lock_open_for_crew(crew.id)
        if assn is not None:
            await self._close_assignment(assn, reason=assignment_reason, ctx=ctx)

        await self._close_crew_row(crew, reason=reason, ctx=ctx)
        return CascadeCloseOutcome(crew=crew, route_assignment=assn)

    async def _close_crew_row(
        self,
        crew: Crew,
        *,
        reason: CrewEndReason,
        ctx: AuditContext,
    ) -> None:
        """Close a locked crew row; caller must have validated state."""
        now = datetime.now(UTC)
        stmt = (
            update(Crew)
            .where(Crew.id == crew.id, Crew.ended_at.is_(None))
            .values(
                ended_at=now,
                ended_by_id=ctx.user_id,
                end_reason=reason.value,
                updated_at=now,
            )
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            raise ConflictError("Crew was modified by another request.")
        crew.ended_at = now
        crew.ended_by_id = ctx.user_id
        crew.end_reason = reason.value

        await self._audit.log(
            action="crew.closed",
            entity_type="crew",
            entity_id=crew.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"ended_at": None},
            new_value={"ended_at": now.isoformat(), "reason": reason.value},
            ip_address=ctx.ip_address or self._ip_address,
            user_agent=ctx.user_agent or self._user_agent,
            category=AuditCategory.FLEET,
            event_type=AuditEventType.CREW_CLOSED,
        )
        logger.info("crew.closed", crew_id=crew.id, reason=reason.value)

    async def _close_assignment(
        self,
        assn: RouteCrewAssignment,
        *,
        reason: RouteCrewUnassignReason,
        ctx: AuditContext,
    ) -> RouteCrewAssignment:
        """Close a locked route-crew assignment row."""
        now = datetime.now(UTC)
        stmt = (
            update(RouteCrewAssignment)
            .where(
                RouteCrewAssignment.id == assn.id,
                RouteCrewAssignment.unassigned_at.is_(None),
            )
            .values(
                unassigned_at=now,
                unassigned_by_id=ctx.user_id,
                reason=reason.value,
                updated_at=now,
            )
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            raise ConflictError("Route assignment was modified by another request.")
        assn.unassigned_at = now
        assn.unassigned_by_id = ctx.user_id
        assn.reason = reason.value

        await self._audit.log(
            action="route_crew.unassigned",
            entity_type="route_crew_assignment",
            entity_id=assn.id,
            user_id=ctx.user_id,
            user_role=ctx.user_role,
            old_value={"unassigned_at": None},
            new_value={
                "unassigned_at": now.isoformat(),
                "reason": reason.value,
                "route_id": assn.route_id,
                "crew_id": assn.crew_id,
            },
            ip_address=ctx.ip_address or self._ip_address,
            user_agent=ctx.user_agent or self._user_agent,
            category=AuditCategory.FLEET,
            event_type=AuditEventType.ROUTE_CREW_UNASSIGNED,
        )
        logger.info(
            "route_crew.unassigned",
            assignment_id=assn.id,
            route_id=assn.route_id,
            crew_id=assn.crew_id,
            reason=reason.value,
        )
        return assn
