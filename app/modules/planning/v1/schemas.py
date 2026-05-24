"""Planning v1 schemas — route map payload."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.common.schemas import BaseSchema


class RouteMapDepot(BaseSchema):
    """Origin of the route — depot/warehouse the driver dispatches from."""

    id: str
    name: str | None = None
    code: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class RouteMapStop(BaseSchema):
    """One ordered stop on the route, with planned and traveled info."""

    route_stop_id: str
    delivery_stop_id: str | None = None
    order_id: str | None = Field(
        default=None,
        description="Set for PICKUP route stops; references the Order whose pickup_address this stop visits.",
    )
    sequence: int
    stop_flow_type: str
    status: str
    tracking_id: str | None = None
    label: str | None = Field(default=None, description="Short human label, e.g. recipient name or address line.")
    latitude: float | None = None
    longitude: float | None = None
    actual_arrival: datetime | None = None
    traveled_encoded_polyline: str | None = Field(
        default=None,
        description="OSRM-encoded polyline of the driven leg arriving AT this stop (populated on route completion).",
    )
    traveled_distance_m: int | None = None
    traveled_duration_s: int | None = None
    traveled_started_at: datetime | None = None
    traveled_ended_at: datetime | None = None


class RouteMapPlannedNavigation(BaseSchema):
    """Planned polyline computed on-demand via OSRM and Redis-cached. Not persisted on the row."""

    encoded_polyline: str | None = None
    geometry_format: str = "polyline"
    distance_m: float | None = None
    duration_s: float | None = None
    fingerprint: str
    cache_hit: bool
    computed_at: datetime


class RouteMapLiveTrail(BaseSchema):
    """Latest live position pulled from ``LOCATION_PING`` events while the route is ACTIVE."""

    latitude: float | None = None
    longitude: float | None = None
    recorded_at: datetime | None = None
    ping_count: int = 0


class RouteMapTraveledSummary(BaseSchema):
    """Top-level summary of the materialised driven history (post-completion)."""

    total_distance_m: int
    total_duration_s: int
    total_points: int


class RouteMapResponse(BaseSchema):
    """Unified map payload across the route lifecycle (assigned → active → completed).

    * ``planned`` is always present (computed from OSRM, cached by fingerprint).
    * ``live_trail`` is present while the route is ``ACTIVE``.
    * ``traveled`` is present once the route is ``COMPLETED`` and history has been materialised.
    """

    route_id: str
    route_code: str
    status: str
    route_type: str
    depot: RouteMapDepot | None = None
    stops: list[RouteMapStop]
    planned: RouteMapPlannedNavigation
    live_trail: RouteMapLiveTrail | None = None
    traveled: RouteMapTraveledSummary | None = None
