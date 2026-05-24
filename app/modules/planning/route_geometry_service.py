"""Route geometry service — planned polyline (on-demand + Redis) and traveled history (per-leg OSRM ``/match``).

Design summary
==============
* **Planned polyline** is **never** persisted on the ``routes`` table. Each ``GET /map`` reads the
  ordered ``route_stops``, computes a stable fingerprint (depot + ``(sequence, route_stop_id)``),
  and either returns a Redis-cached OSRM ``/route`` response or computes-and-caches it.
* **Traveled history** is materialised once when a route flips to ``COMPLETED``. For each leg
  ``prev_stop → curr_stop`` we pull the ``LOCATION_PING`` ``route_events`` between the two stop
  arrivals, run OSRM ``/match``, and persist the small encoded polyline + summary on the matching
  ``route_stops`` row. If a leg has too few pings (< 2) we fall back to OSRM ``/route`` between the
  planned coordinates so the history is still drawable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.constants import CACHE_TTL_OSRM_ROUTE
from app.common.enums import LogEvent
from app.common.exceptions import NotFoundError, ValidationError
from app.common.service import BaseService
from app.core.redis import get_redis
from app.integrations.osrm import (
    format_coordinate_path,
    match,
)
from app.integrations.osrm import (
    route as osrm_route,
)
from app.modules.depots.models import Depot
from app.modules.orders.models import DeliveryStop, Order
from app.modules.pickup_addresses.models import PickupAddress
from app.modules.planning.models import Route, RouteEvent, RouteStop
from app.modules.planning.route_navigation import compute_route_navigation_fingerprint

logger = structlog.get_logger()

_LOCATION_PING_EVENT_TYPE = "LOCATION_PING"
_PLANNED_CACHE_PREFIX = "osrm:planned_route:v1:"
_MIN_PINGS_FOR_MATCH = 4
_MIN_PINGS_FOR_FALLBACK = 2
_MAX_PINGS_PER_LEG = 100


@dataclass(frozen=True, slots=True)
class PlannedStop:
    """Lightweight projection of a route stop with its planned coordinates.

    Coordinates come from one of two sources depending on the row:
    * a delivery_stop (``delivery_stop_id`` set on the route_stop), or
    * an order's pickup_address (``order_id`` set on the route_stop, pickup-flow).
    """

    route_stop_id: str
    delivery_stop_id: str | None
    order_id: str | None
    sequence: int
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class PlannedGeometry:
    """Result returned by ``get_or_compute_planned_geometry``."""

    encoded_polyline: str | None
    geometry_format: str
    distance_m: float | None
    duration_s: float | None
    legs: list[dict[str, float | None]]
    fingerprint: str
    cache_hit: bool
    computed_at: datetime


@dataclass(slots=True)
class TraveledLegOutcome:
    route_stop_id: str
    sequence: int
    encoded_polyline: str | None
    distance_m: int | None
    duration_s: int | None
    started_at: datetime | None
    ended_at: datetime | None
    source: str
    point_count: int


@dataclass(slots=True)
class TraveledHistoryOutcome:
    legs: list[TraveledLegOutcome]
    total_distance_m: int
    total_duration_s: int
    total_points: int


class RouteGeometryService(BaseService):
    """Reads ``route_stops``, talks to OSRM, caches in Redis, persists per-leg history."""

    def __init__(self, session: AsyncSession, request: Any | None = None) -> None:
        super().__init__(session, request)
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def get_or_compute_planned_geometry(self, route_id: str) -> PlannedGeometry:
        """Return the planned polyline from depot through ordered stops.

        Reads cache first; on miss, calls OSRM ``/route`` and writes the encoded result back
        to Redis under ``osrm:planned_route:v1:{fingerprint}``.
        """
        _route, planned_stops, depot, _rows = await self._load_route_context(route_id)
        if not planned_stops:
            raise ValidationError("Route has no stops with coordinates")

        fingerprint = compute_route_navigation_fingerprint(
            sequences_and_route_stop_ids=[(s.sequence, s.route_stop_id) for s in planned_stops]
        )

        cached = await self._read_planned_cache(fingerprint)
        if cached is not None:
            return PlannedGeometry(
                encoded_polyline=cached.get("encoded_polyline"),
                geometry_format=str(cached.get("geometry_format") or "polyline"),
                distance_m=cached.get("distance_m"),
                duration_s=cached.get("duration_s"),
                legs=list(cached.get("legs") or []),
                fingerprint=fingerprint,
                cache_hit=True,
                computed_at=datetime.fromisoformat(str(cached.get("computed_at"))),
            )

        coordinates: list[tuple[float, float]] = []
        if depot and depot.latitude is not None and depot.longitude is not None:
            coordinates.append((float(depot.longitude), float(depot.latitude)))
        for s in planned_stops:
            coordinates.append((s.longitude, s.latitude))

        if len(coordinates) < 2:
            raise ValidationError("At least two coordinates required to plan a route")

        try:
            response = await osrm_route(
                coordinates,
                overview="full",
                geometries="polyline",
                steps=False,
            )
        except ValidationError:
            logger.error(
                LogEvent.OSRM_PLANNED_GEOMETRY_FAILED,
                route_id=route_id,
                fingerprint=fingerprint,
                coordinates_count=len(coordinates),
            )
            raise

        first = (response.get("routes") or [{}])[0]
        encoded_polyline = first.get("geometry") if isinstance(first.get("geometry"), str) else None
        distance_m = float(first.get("distance")) if first.get("distance") is not None else None
        duration_s = float(first.get("duration")) if first.get("duration") is not None else None
        legs_out: list[dict[str, float | None]] = []
        for leg in first.get("legs") or []:
            if not isinstance(leg, dict):
                continue
            legs_out.append(
                {
                    "distance_m": float(leg.get("distance")) if leg.get("distance") is not None else None,
                    "duration_s": float(leg.get("duration")) if leg.get("duration") is not None else None,
                }
            )

        computed_at = datetime.now(UTC)
        payload = {
            "encoded_polyline": encoded_polyline,
            "geometry_format": "polyline",
            "distance_m": distance_m,
            "duration_s": duration_s,
            "legs": legs_out,
            "computed_at": computed_at.isoformat(),
        }
        await self._write_planned_cache(fingerprint, payload)

        logger.info(
            LogEvent.OSRM_PLANNED_GEOMETRY_COMPUTED,
            route_id=route_id,
            fingerprint=fingerprint,
            stops=len(planned_stops),
            distance_m=distance_m,
            duration_s=duration_s,
        )

        return PlannedGeometry(
            encoded_polyline=encoded_polyline,
            geometry_format="polyline",
            distance_m=distance_m,
            duration_s=duration_s,
            legs=legs_out,
            fingerprint=fingerprint,
            cache_hit=False,
            computed_at=computed_at,
        )

    async def compute_traveled_history(self, route_id: str) -> TraveledHistoryOutcome:
        """Map-match ``LOCATION_PING`` events per leg and persist on ``route_stops``.

        Idempotent: rows already carrying ``traveled_encoded_polyline`` are recomputed and
        overwritten (useful when fixing data or re-running for QA).
        """
        _route, planned_stops, depot, _rows = await self._load_route_context(route_id)
        if not planned_stops:
            raise ValidationError("Route has no stops to materialise history for")
        by_route_stop_id = {s.route_stop_id: s for s in planned_stops}

        route_started_at = await self._route_start_event_at(route_id)
        depot_origin: tuple[float, float] | None = None
        if depot and depot.latitude is not None and depot.longitude is not None:
            depot_origin = (float(depot.longitude), float(depot.latitude))

        leg_outcomes: list[TraveledLegOutcome] = []
        total_distance = 0
        total_duration = 0
        total_points = 0

        prev_coord: tuple[float, float] | None = depot_origin
        prev_time: datetime | None = route_started_at

        rs_rows = (
            await self._session.execute(
                select(RouteStop).where(RouteStop.route_id == route_id).order_by(RouteStop.sequence.asc())
            )
        ).scalars().all()
        for rs in rs_rows:
            seq = int(rs.sequence)
            projected = by_route_stop_id.get(rs.id)
            if projected is None:
                continue
            curr_coord = (projected.longitude, projected.latitude)
            curr_time = rs.actual_arrival

            outcome = await self._compute_leg_outcome(
                route_id=route_id,
                route_stop=rs,
                sequence=seq,
                prev_coord=prev_coord,
                curr_coord=curr_coord,
                prev_time=prev_time,
                curr_time=curr_time,
            )

            await self._persist_leg(route_stop=rs, outcome=outcome)
            leg_outcomes.append(outcome)
            if outcome.distance_m:
                total_distance += outcome.distance_m
            if outcome.duration_s:
                total_duration += outcome.duration_s
            total_points += outcome.point_count

            prev_coord = curr_coord
            prev_time = curr_time

        await self._session.commit()

        logger.info(
            LogEvent.OSRM_TRAVELED_HISTORY_COMPUTED,
            route_id=route_id,
            legs=len(leg_outcomes),
            total_distance_m=total_distance,
            total_duration_s=total_duration,
            total_points=total_points,
        )
        return TraveledHistoryOutcome(
            legs=leg_outcomes,
            total_distance_m=total_distance,
            total_duration_s=total_duration,
            total_points=total_points,
        )

    async def _compute_leg_outcome(
        self,
        *,
        route_id: str,
        route_stop: RouteStop,
        sequence: int,
        prev_coord: tuple[float, float] | None,
        curr_coord: tuple[float, float],
        prev_time: datetime | None,
        curr_time: datetime | None,
    ) -> TraveledLegOutcome:
        points = await self._pings_for_window(
            route_id=route_id,
            start=prev_time,
            end=curr_time,
        )
        downsampled = _downsample(points, _MAX_PINGS_PER_LEG)
        point_count = len(downsampled)

        if point_count >= _MIN_PINGS_FOR_MATCH:
            coords = [(p["lng"], p["lat"]) for p in downsampled]
            try:
                resp = await match(coords, overview="full", geometries="polyline")
                matchings = resp.get("matchings") or []
                if matchings and isinstance(matchings[0], dict):
                    m0 = matchings[0]
                    geom = m0.get("geometry") if isinstance(m0.get("geometry"), str) else None
                    distance_m = int(m0.get("distance")) if m0.get("distance") is not None else None
                    duration_s = int(m0.get("duration")) if m0.get("duration") is not None else None
                    logger.info(
                        LogEvent.OSRM_TRAVELED_LEG_MATCHED,
                        route_id=route_id,
                        sequence=sequence,
                        point_count=point_count,
                        distance_m=distance_m,
                        duration_s=duration_s,
                    )
                    return TraveledLegOutcome(
                        route_stop_id=route_stop.id,
                        sequence=sequence,
                        encoded_polyline=geom,
                        distance_m=distance_m,
                        duration_s=duration_s,
                        started_at=downsampled[0]["at"],
                        ended_at=downsampled[-1]["at"],
                        source="osrm_match",
                        point_count=point_count,
                    )
            except ValidationError:
                logger.warning(
                    LogEvent.OSRM_TRAVELED_LEG_FAILED,
                    route_id=route_id,
                    sequence=sequence,
                    point_count=point_count,
                )

        if prev_coord is not None and point_count < _MIN_PINGS_FOR_FALLBACK:
            try:
                resp = await osrm_route(
                    [prev_coord, curr_coord],
                    overview="full",
                    geometries="polyline",
                )
                first = (resp.get("routes") or [{}])[0]
                geom = first.get("geometry") if isinstance(first.get("geometry"), str) else None
                distance_m = int(first.get("distance")) if first.get("distance") is not None else None
                duration_s = int(first.get("duration")) if first.get("duration") is not None else None
                logger.info(
                    LogEvent.OSRM_TRAVELED_LEG_FALLBACK,
                    route_id=route_id,
                    sequence=sequence,
                    point_count=point_count,
                )
                return TraveledLegOutcome(
                    route_stop_id=route_stop.id,
                    sequence=sequence,
                    encoded_polyline=geom,
                    distance_m=distance_m,
                    duration_s=duration_s,
                    started_at=prev_time,
                    ended_at=curr_time,
                    source="osrm_route_fallback",
                    point_count=point_count,
                )
            except ValidationError:
                logger.warning(
                    LogEvent.OSRM_TRAVELED_LEG_FAILED,
                    route_id=route_id,
                    sequence=sequence,
                    point_count=point_count,
                )

        logger.info(
            LogEvent.OSRM_TRAVELED_HISTORY_NO_POINTS,
            route_id=route_id,
            sequence=sequence,
            point_count=point_count,
        )
        return TraveledLegOutcome(
            route_stop_id=route_stop.id,
            sequence=sequence,
            encoded_polyline=None,
            distance_m=None,
            duration_s=None,
            started_at=downsampled[0]["at"] if downsampled else prev_time,
            ended_at=downsampled[-1]["at"] if downsampled else curr_time,
            source="none",
            point_count=point_count,
        )

    async def _persist_leg(self, *, route_stop: RouteStop, outcome: TraveledLegOutcome) -> None:
        route_stop.traveled_encoded_polyline = outcome.encoded_polyline
        route_stop.traveled_distance_m = outcome.distance_m
        route_stop.traveled_duration_s = outcome.duration_s
        route_stop.traveled_started_at = outcome.started_at
        route_stop.traveled_ended_at = outcome.ended_at
        route_stop.traveled_meta = {
            "source": outcome.source,
            "point_count": outcome.point_count,
            "geometry_format": "polyline",
            "computed_at": datetime.now(UTC).isoformat(),
        }

    async def _load_route_context(
        self, route_id: str
    ) -> tuple[Route, list[PlannedStop], Depot | None, list[tuple[RouteStop, DeliveryStop | None, Order | None, PickupAddress | None]]]:
        """Load the route + ordered stops with coordinates resolved from either source.

        Returns the route, a flat list of ``PlannedStop`` (skipping rows missing coords on both
        sides), the depot (if known), and the raw join rows in case callers need the originals.
        """
        route = (await self._session.execute(select(Route).where(Route.id == route_id))).scalars().first()
        if route is None:
            raise NotFoundError(resource="route", id=route_id)

        stmt = (
            select(RouteStop, DeliveryStop, Order, PickupAddress)
            .outerjoin(DeliveryStop, DeliveryStop.id == RouteStop.delivery_stop_id)
            .outerjoin(Order, Order.id == RouteStop.order_id)
            .outerjoin(PickupAddress, PickupAddress.id == Order.pickup_address_id)
            .where(RouteStop.route_id == route_id)
            .order_by(RouteStop.sequence.asc())
        )
        rows = list((await self._session.execute(stmt)).all())
        raw: list[tuple[RouteStop, DeliveryStop | None, Order | None, PickupAddress | None]] = [
            (rs, ds, order, pa) for rs, ds, order, pa in rows
        ]

        planned: list[PlannedStop] = []
        for rs, ds, _order, pa in raw:
            lat, lng = self._resolve_coords(ds, pa)
            if lat is None or lng is None:
                continue
            planned.append(
                PlannedStop(
                    route_stop_id=rs.id,
                    delivery_stop_id=ds.id if ds is not None else None,
                    order_id=rs.order_id,
                    sequence=int(rs.sequence),
                    latitude=lat,
                    longitude=lng,
                )
            )

        depot: Depot | None = None
        depot_id = await self._infer_depot_id(route)
        if depot_id:
            depot = (await self._session.execute(select(Depot).where(Depot.id == depot_id))).scalars().first()
        return route, planned, depot, raw

    @staticmethod
    def _resolve_coords(
        ds: DeliveryStop | None,
        pa: PickupAddress | None,
    ) -> tuple[float | None, float | None]:
        """Pickup_address takes precedence when present (pickup-flow stops with order_id);
        delivery_stop is the fallback for delivery-flow stops."""
        if pa is not None and pa.latitude is not None and pa.longitude is not None:
            return float(pa.latitude), float(pa.longitude)
        if ds is not None and ds.latitude is not None and ds.longitude is not None:
            return float(ds.latitude), float(ds.longitude)
        return None, None

    async def _infer_depot_id(self, route: Route) -> str | None:
        if route.plan_id:
            from app.modules.planning.models import RoutePlan

            plan = (
                await self._session.execute(select(RoutePlan).where(RoutePlan.id == route.plan_id))
            ).scalars().first()
            if plan is not None:
                return plan.depot_id
        if route.driver_id:
            from app.modules.drivers.models import Driver

            driver = (
                await self._session.execute(select(Driver).where(Driver.id == route.driver_id))
            ).scalars().first()
            if driver is not None and driver.depot_id:
                return driver.depot_id
        return None

    async def _pings_for_window(
        self,
        *,
        route_id: str,
        start: datetime | None,
        end: datetime | None,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(RouteEvent)
            .where(
                RouteEvent.route_id == route_id,
                RouteEvent.event_type == _LOCATION_PING_EVENT_TYPE,
                RouteEvent.lat.is_not(None),
                RouteEvent.lng.is_not(None),
            )
            .order_by(RouteEvent.occurred_at.asc())
        )
        if start is not None:
            stmt = stmt.where(RouteEvent.occurred_at >= start)
        if end is not None:
            stmt = stmt.where(RouteEvent.occurred_at <= end)
        rows = list((await self._session.execute(stmt)).scalars().all())
        return [
            {
                "lat": float(r.lat),  # type: ignore[arg-type]
                "lng": float(r.lng),  # type: ignore[arg-type]
                "at": r.occurred_at,
            }
            for r in rows
        ]

    async def _route_start_event_at(self, route_id: str) -> datetime | None:
        stmt = (
            select(RouteEvent.occurred_at)
            .where(RouteEvent.route_id == route_id, RouteEvent.event_type == "ROUTE_STARTED")
            .order_by(RouteEvent.occurred_at.asc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _read_planned_cache(self, fingerprint: str) -> dict[str, Any] | None:
        try:
            redis = get_redis()
        except RuntimeError:
            return None
        raw = await redis.get(f"{_PLANNED_CACHE_PREFIX}{fingerprint}")
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return data

    async def _write_planned_cache(self, fingerprint: str, payload: dict[str, Any]) -> None:
        try:
            redis = get_redis()
        except RuntimeError:
            return
        await redis.set(
            f"{_PLANNED_CACHE_PREFIX}{fingerprint}",
            json.dumps(payload),
            ex=CACHE_TTL_OSRM_ROUTE,
        )
        logger.info(
            LogEvent.OSRM_PLANNED_GEOMETRY_CACHED,
            fingerprint=fingerprint,
            ttl=CACHE_TTL_OSRM_ROUTE,
        )

    @classmethod
    def for_session(cls, session: AsyncSession) -> RouteGeometryService:
        return cls(session, None)


def format_planned_coordinates_for_log(coordinates: list[tuple[float, float]]) -> str:
    """Compact representation of an OSRM coordinate path for diagnostic logging."""
    return format_coordinate_path(coordinates)


def _downsample(points: list[dict[str, Any]], max_size: int) -> list[dict[str, Any]]:
    if len(points) <= max_size:
        return points
    step = len(points) / max_size
    indices = [int(i * step) for i in range(max_size)]
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for idx in indices:
        if idx in seen:
            continue
        seen.add(idx)
        out.append(points[idx])
    if points[-1] is not out[-1]:
        out.append(points[-1])
    return out
