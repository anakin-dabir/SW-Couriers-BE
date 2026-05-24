"""Planning service — read-side orchestration for routes (map payload, geometry).

This service intentionally stays read-only at the moment. Write paths (creating route plans,
publishing them, assigning stops) live alongside the planning engine work; this module gives the
API layer a single entry point for the **route map** view across the route's lifecycle.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import UserRole
from app.common.exceptions import ForbiddenError, NotFoundError
from app.common.service import BaseService
from app.modules.depots.models import Depot
from app.modules.drivers.models import Driver
from app.modules.orders.models import DeliveryStop, Order
from app.modules.pickup_addresses.models import PickupAddress
from app.modules.planning.models import Route, RouteEvent, RouteStop
from app.modules.planning.route_geometry_service import RouteGeometryService


class PlanningService(BaseService):
    """Read-only orchestration for route map and geometry."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._session = session
        self._geom = RouteGeometryService.for_session(session)

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def get_route_map(
        self,
        *,
        route_id: str,
        viewer_user_id: str,
        viewer_role: UserRole | str,
    ) -> dict[str, Any]:
        """Return the unified map payload for a route (planned + live trail + traveled history).

        Authorisation rule: drivers can only read routes they're assigned to; everyone else with
        access to this endpoint can read any route (ops/admin/dispatcher). Ownership is resolved
        through ``drivers.user_id`` so we don't leak driver IDs to clients.
        """
        route = await self._session.get(Route, route_id)
        if route is None:
            raise NotFoundError(resource="route", id=route_id)

        await self._ensure_can_read(
            route=route,
            viewer_user_id=viewer_user_id,
            viewer_role=str(viewer_role),
        )

        stmt = (
            select(RouteStop, DeliveryStop, Order, PickupAddress)
            .outerjoin(DeliveryStop, DeliveryStop.id == RouteStop.delivery_stop_id)
            .outerjoin(Order, Order.id == RouteStop.order_id)
            .outerjoin(PickupAddress, PickupAddress.id == Order.pickup_address_id)
            .where(RouteStop.route_id == route_id)
            .order_by(RouteStop.sequence.asc())
        )
        raw_rows = (await self._session.execute(stmt)).all()
        rows: list[tuple[RouteStop, DeliveryStop | None, Order | None, PickupAddress | None]] = [
            (r[0], r[1], r[2], r[3]) for r in raw_rows
        ]

        planned = await self._geom.get_or_compute_planned_geometry(route_id)
        depot = await self._resolve_depot(route)

        status = str(route.status or "").upper()
        stops_out = self._project_stops(rows)

        live_trail = await self._build_live_trail(route_id) if status == "ACTIVE" else None
        traveled = await self._build_traveled_summary(route_id, stops_out) if status == "COMPLETED" else None

        return {
            "route_id": route.id,
            "route_code": route.route_code,
            "status": status,
            "route_type": str(route.route_type),
            "depot": self._depot_payload(depot),
            "stops": stops_out,
            "planned": {
                "encoded_polyline": planned.encoded_polyline,
                "geometry_format": planned.geometry_format,
                "distance_m": planned.distance_m,
                "duration_s": planned.duration_s,
                "fingerprint": planned.fingerprint,
                "cache_hit": planned.cache_hit,
                "computed_at": planned.computed_at,
            },
            "live_trail": live_trail,
            "traveled": traveled,
        }

    async def _ensure_can_read(self, *, route: Route, viewer_user_id: str, viewer_role: str) -> None:
        if viewer_role == UserRole.DRIVER.value:
            stmt = select(Driver.id).where(Driver.user_id == viewer_user_id)
            driver_id = (await self._session.execute(stmt)).scalar_one_or_none()
            if driver_id is None or route.driver_id != driver_id:
                raise ForbiddenError("You don't have access to this route")

    async def _resolve_depot(self, route: Route) -> Depot | None:
        depot_id = await self._geom._infer_depot_id(route)  # type: ignore[attr-defined]
        if not depot_id:
            return None
        return (await self._session.execute(select(Depot).where(Depot.id == depot_id))).scalars().first()

    def _project_stops(
        self,
        rows: list[tuple[RouteStop, DeliveryStop | None, Order | None, PickupAddress | None]],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for rs, ds, order, pa in rows:
            lat: float | None
            lng: float | None
            label: str | None
            tracking_id: str | None
            delivery_stop_id: str | None
            if pa is not None and pa.latitude is not None and pa.longitude is not None:
                lat = float(pa.latitude)
                lng = float(pa.longitude)
                label = pa.label or pa.line_1
                tracking_id = order.order_id if order is not None else None
                delivery_stop_id = None
            else:
                lat = float(ds.latitude) if ds is not None and ds.latitude is not None else None
                lng = float(ds.longitude) if ds is not None and ds.longitude is not None else None
                first = (getattr(ds, "recipient_first_name", None) or "").strip() if ds else ""
                last = (getattr(ds, "recipient_last_name", None) or "").strip() if ds else ""
                recipient = " ".join(p for p in (first, last) if p) or None
                label = recipient or (getattr(ds, "line_1", None) if ds else None)
                tracking_id = getattr(ds, "tracking_id", None)
                delivery_stop_id = getattr(ds, "id", None)
            out.append(
                {
                    "route_stop_id": rs.id,
                    "delivery_stop_id": delivery_stop_id,
                    "order_id": rs.order_id,
                    "sequence": int(rs.sequence),
                    "stop_flow_type": str(rs.stop_flow_type),
                    "status": str(rs.status),
                    "tracking_id": tracking_id,
                    "label": label,
                    "latitude": lat,
                    "longitude": lng,
                    "actual_arrival": rs.actual_arrival,
                    "traveled_encoded_polyline": rs.traveled_encoded_polyline,
                    "traveled_distance_m": rs.traveled_distance_m,
                    "traveled_duration_s": rs.traveled_duration_s,
                    "traveled_started_at": rs.traveled_started_at,
                    "traveled_ended_at": rs.traveled_ended_at,
                }
            )
        return out

    async def _build_live_trail(self, route_id: str) -> dict[str, Any]:
        latest_stmt = (
            select(RouteEvent)
            .where(
                RouteEvent.route_id == route_id,
                RouteEvent.event_type == "LOCATION_PING",
                RouteEvent.lat.is_not(None),
                RouteEvent.lng.is_not(None),
            )
            .order_by(RouteEvent.occurred_at.desc())
            .limit(1)
        )
        latest = (await self._session.execute(latest_stmt)).scalars().first()
        count_stmt = (
            select(func.count())
            .select_from(RouteEvent)
            .where(RouteEvent.route_id == route_id, RouteEvent.event_type == "LOCATION_PING")
        )
        ping_count = int((await self._session.execute(count_stmt)).scalar_one() or 0)
        return {
            "latitude": float(latest.lat) if latest and latest.lat is not None else None,
            "longitude": float(latest.lng) if latest and latest.lng is not None else None,
            "recorded_at": latest.occurred_at if latest else None,
            "ping_count": ping_count,
        }

    async def _build_traveled_summary(
        self,
        route_id: str,
        stops: list[dict[str, Any]],
    ) -> dict[str, int]:
        total_distance = sum(int(s["traveled_distance_m"] or 0) for s in stops)
        total_duration = sum(int(s["traveled_duration_s"] or 0) for s in stops)
        count_stmt = (
            select(func.count())
            .select_from(RouteEvent)
            .where(RouteEvent.route_id == route_id, RouteEvent.event_type == "LOCATION_PING")
        )
        total_points = int((await self._session.execute(count_stmt)).scalar_one() or 0)
        return {
            "total_distance_m": total_distance,
            "total_duration_s": total_duration,
            "total_points": total_points,
        }

    @staticmethod
    def _depot_payload(depot: Depot | None) -> dict[str, Any] | None:
        if depot is None:
            return None
        return {
            "id": depot.id,
            "name": depot.name,
            "code": depot.code,
            "latitude": depot.latitude,
            "longitude": depot.longitude,
        }
