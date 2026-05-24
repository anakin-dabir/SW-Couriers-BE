"""Planning v1 OpenAPI doc entries."""

from __future__ import annotations

from app.core.swagger.utils import (
    create_doc_entry,
    error_401_entry,
    error_entry,
    success_entry,
)

ROUTE_MAP = create_doc_entry(
    "Get unified route map payload (planned + live + traveled)",
    {
        200: success_entry(
            "Route map payload",
            data={
                "route_id": "8b59b8f3-5e58-4cf3-9c5e-cd0ccb1f9c61",
                "route_code": "RT-001",
                "status": "COMPLETED",
                "route_type": "PICKUP",
                "depot": {
                    "id": "77777777-7777-4777-8777-000000000107",
                    "name": "SWC London Depot",
                    "code": "LDN-001",
                    "latitude": 51.5267,
                    "longitude": -0.0119,
                },
                "stops": [
                    {
                        "route_stop_id": "11111111-2222-4333-8444-000000000001",
                        "delivery_stop_id": "22222222-3333-4444-8555-000000000001",
                        "sequence": 1,
                        "stop_flow_type": "PICKUP",
                        "status": "COMPLETED",
                        "tracking_id": "ST-00000001",
                        "label": "Canary Wharf",
                        "latitude": 51.5054,
                        "longitude": -0.0235,
                        "actual_arrival": "2026-05-14T09:42:00+00:00",
                        "traveled_encoded_polyline": "yvw}Hho`@d@_FbAcH",
                        "traveled_distance_m": 2820,
                        "traveled_duration_s": 540,
                        "traveled_started_at": "2026-05-14T09:30:00+00:00",
                        "traveled_ended_at": "2026-05-14T09:42:00+00:00",
                    }
                ],
                "planned": {
                    "encoded_polyline": "_p~iF~ps|U_ulLnnqCwhqCjwjK",
                    "geometry_format": "polyline",
                    "distance_m": 12450.0,
                    "duration_s": 2340.0,
                    "fingerprint": "8c1d9f5e1c70…",
                    "cache_hit": True,
                    "computed_at": "2026-05-14T09:00:00+00:00",
                },
                "live_trail": None,
                "traveled": {
                    "total_distance_m": 12080,
                    "total_duration_s": 2300,
                    "total_points": 142,
                },
            },
        ),
        401: error_401_entry(),
        403: error_entry(
            "Driver tried to view a route they don't own",
            code="FORBIDDEN",
            message="You don't have access to this route",
        ),
        404: error_entry("Route not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description=(
        "Single endpoint to render a route on a map across its lifecycle. "
        "**``planned``** is always present — computed from OSRM ``/route`` (depot through ordered stops) and cached in Redis "
        "by a stable fingerprint over ``(sequence, route_stop_id)``; ``cache_hit`` indicates whether this response came from "
        "cache. **``live_trail``** is present when the route is ``ACTIVE`` and reflects the most recent ``LOCATION_PING``. "
        "**``traveled``** is materialised once the route flips to ``COMPLETED`` (per-leg OSRM ``/match`` results persisted on "
        "``route_stops.traveled_encoded_polyline``). Drivers may only read their own routes; ADMIN/DISPATCHER/OPS can read any. "
        "Polylines are Google-encoded; decode with any standard polyline library for rendering."
    ),
)
