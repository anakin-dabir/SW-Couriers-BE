from __future__ import annotations

from typing import Any, Literal

import httpx

from app.common.exceptions import ValidationError
from app.core.config import settings

OSRMProfile = Literal["driving", "walking", "cycling"]
RouteOverview = Literal["full", "simplified", "false"]
RouteGeometries = Literal["polyline", "polyline6", "geojson"]

_TIMEOUT_ROUTE = 30.0
_TIMEOUT_TABLE = 120.0
_TIMEOUT_MATCH = 45.0
_TIMEOUT_NEAREST = 15.0


def _base_url() -> str:
    base = (settings.OSRM_BASE_URL or "").strip().rstrip("/")
    if not base:
        raise ValidationError("OSRM is not configured")
    return base


def format_coordinate_path(coordinates: list[tuple[float, float]]) -> str:
    """Build the ``lon,lat;lon,lat`` path segment used in OSRM v1 URLs."""
    if not coordinates:
        raise ValidationError("At least one coordinate is required")
    return ";".join(f"{lon},{lat}" for lon, lat in coordinates)


def _raise_for_osrm_code(data: dict[str, Any]) -> None:
    code = data.get("code")
    if code == "Ok":
        return
    msg = str(data.get("message") or code or "OSRM request failed")
    raise ValidationError(msg)


async def _get(path: str, params: dict[str, str] | None, *, timeout: float) -> dict[str, Any]:
    url = f"{_base_url()}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, params=params)
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ValidationError("OSRM service request failed") from exc
    except httpx.RequestError as exc:
        raise ValidationError("OSRM service unavailable") from exc
    data = r.json()
    if not isinstance(data, dict):
        raise ValidationError("OSRM returned an invalid response")
    return data


async def route(
    coordinates: list[tuple[float, float]],
    *,
    profile: OSRMProfile = "driving",
    overview: RouteOverview = "full",
    geometries: RouteGeometries = "geojson",
    steps: bool = False,
    alternatives: bool = False,
    continue_straight: bool | None = None,
) -> dict[str, Any]:
    """Compute road routes between an ordered sequence of lon/lat waypoints via OSRM's HTTP Route service.

    Sends GET ``{base}/route/v1/{profile}/{lon1},{lat1};...`` with query parameters documented at
    https://project-osrm.org/. Coordinates are **longitude, latitude** (EPSG:4326), in visit order.

    Returns the parsed JSON (``code``, ``routes``, ``waypoints``). Use :func:`first_route_geometry` or
    :func:`route_linestring_coordinates` to extract a polyline for maps when ``geometries=geojson``.

    Raises:
        ValidationError: Missing config, bad input, transport error, or OSRM ``code`` other than ``Ok``.
    """
    if len(coordinates) < 2:
        raise ValidationError("At least two coordinates are required for routing")
    coord_path = format_coordinate_path(coordinates)
    params: dict[str, str] = {
        "overview": overview,
        "geometries": geometries,
        "steps": "true" if steps else "false",
        "alternatives": "true" if alternatives else "false",
    }
    if continue_straight is not None:
        params["continue_straight"] = "true" if continue_straight else "false"
    data = await _get(f"/route/v1/{profile}/{coord_path}", params, timeout=_TIMEOUT_ROUTE)
    _raise_for_osrm_code(data)
    return data


async def table(
    coordinates: list[tuple[float, float]],
    *,
    profile: OSRMProfile = "driving",
    annotations: str = "duration,distance",
    sources: list[int] | None = None,
    destinations: list[int] | None = None,
    fallback_speed: float | None = None,
) -> dict[str, Any]:
    """Build a travel time / distance matrix between coordinates using OSRM's Table service.

    Sends GET ``{base}/table/v1/{profile}/{coordinates}``. Indices in ``sources`` and ``destinations``
    refer to positions in the ``coordinates`` list. When omitted, OSRM uses all pairs.

    Raises:
        ValidationError: Missing config, empty coordinates, transport error, or OSRM ``code`` not ``Ok``.
    """
    if not coordinates:
        raise ValidationError("At least one coordinate is required for the distance table")
    coord_path = format_coordinate_path(coordinates)
    params: dict[str, str] = {"annotations": annotations}
    if sources is not None:
        params["sources"] = ";".join(str(i) for i in sources)
    if destinations is not None:
        params["destinations"] = ";".join(str(i) for i in destinations)
    if fallback_speed is not None:
        params["fallback_speed"] = str(fallback_speed)
    data = await _get(f"/table/v1/{profile}/{coord_path}", params, timeout=_TIMEOUT_TABLE)
    _raise_for_osrm_code(data)
    return data


async def match(
    coordinates: list[tuple[float, float]],
    *,
    profile: OSRMProfile = "driving",
    overview: RouteOverview = "full",
    geometries: RouteGeometries = "geojson",
    steps: bool = False,
) -> dict[str, Any]:
    """Map-match a GPS trace to the road network via OSRM's Match service.

    Use for driver history or noisy breadcrumbs; returns a merged route geometry and matched waypoints.

    Raises:
        ValidationError: Fewer than two points, missing config, transport error, or OSRM failure code.
    """
    if len(coordinates) < 2:
        raise ValidationError("At least two coordinates are required for map matching")
    coord_path = format_coordinate_path(coordinates)
    params: dict[str, str] = {
        "overview": overview,
        "geometries": geometries,
        "steps": "true" if steps else "false",
    }
    data = await _get(f"/match/v1/{profile}/{coord_path}", params, timeout=_TIMEOUT_MATCH)
    _raise_for_osrm_code(data)
    return data


async def nearest(
    coordinate: tuple[float, float],
    *,
    profile: OSRMProfile = "driving",
    number: int = 1,
) -> dict[str, Any]:
    """Find the nearest routable road segment for a lon/lat point via OSRM's Nearest service.

    Raises:
        ValidationError: Missing config, transport error, or OSRM failure code.
    """
    lon, lat = coordinate
    params = {"number": str(number)}
    data = await _get(f"/nearest/v1/{profile}/{lon},{lat}", params, timeout=_TIMEOUT_NEAREST)
    _raise_for_osrm_code(data)
    return data


def first_route_geometry(route_response: dict[str, Any]) -> dict[str, Any] | None:
    """Return GeoJSON geometry dict for the first route in a successful OSRM Route response, or None."""
    routes = route_response.get("routes")
    if not routes or not isinstance(routes, list):
        return None
    first = routes[0]
    if not isinstance(first, dict):
        return None
    geom = first.get("geometry")
    return geom if isinstance(geom, dict) else None


def route_linestring_coordinates(route_response: dict[str, Any]) -> list[list[float]] | None:
    """Extract ``LineString`` coordinates ``[[lon, lat], ...]`` from the first route, if present."""
    geom = first_route_geometry(route_response)
    if not geom or geom.get("type") != "LineString":
        return None
    coords = geom.get("coordinates")
    if not isinstance(coords, list):
        return None
    return coords


def route_summary(route_response: dict[str, Any]) -> dict[str, float | None] | None:
    """Return distance (metres) and duration (seconds) from the first route, if available."""
    routes = route_response.get("routes")
    if not routes or not isinstance(routes, list):
        return None
    first = routes[0]
    if not isinstance(first, dict):
        return None
    dist = first.get("distance")
    dur = first.get("duration")
    return {
        "distance": float(dist) if dist is not None else None,
        "duration": float(dur) if dur is not None else None,
    }
