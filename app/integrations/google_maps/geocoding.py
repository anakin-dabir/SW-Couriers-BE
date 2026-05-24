from __future__ import annotations

from typing import Any

import httpx

from app.common.exceptions import ValidationError
from app.core.config import settings

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_TIMEOUT = 12.0


def _first_long_name(components: list[dict[str, Any]], *types: str) -> str | None:
    want = set(types)
    for comp in components:
        tset = set(comp.get("types") or [])
        if tset & want:
            return comp.get("long_name") or comp.get("short_name")
    return None


def _parse_address_components(components: list[dict[str, Any]]) -> dict[str, str | None]:
    street_num = _first_long_name(components, "street_number")
    route = _first_long_name(components, "route")
    line_1_parts = [p for p in [street_num, route] if p]
    line_1 = " ".join(line_1_parts) if line_1_parts else None
    city = _first_long_name(
        components,
        "postal_town",
        "locality",
        "sublocality",
    )
    state = _first_long_name(components, "administrative_area_level_2", "administrative_area_level_1")
    postcode = _first_long_name(components, "postal_code")
    country = _first_long_name(components, "country")
    return {
        "line_1": line_1,
        "city": city,
        "state": state,
        "postcode": postcode,
        "country": country,
    }


async def _get_json(params: dict[str, str]) -> dict[str, Any]:
    key = settings.GOOGLE_MAPS_API_KEY.get_secret_value()
    if not key.strip():
        raise ValidationError("Google Maps geocoding is not configured")
    params = {**params, "key": key}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(_GEOCODE_URL, params=params)
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ValidationError("Geocoding service request failed") from exc
    except httpx.RequestError as exc:
        raise ValidationError("Geocoding service unavailable") from exc
    return r.json()


async def forward_geocode(address: str) -> dict[str, Any]:
    q = address.strip()
    if not q:
        raise ValidationError("Address query is empty")
    data = await _get_json({"address": q, "region": "gb"})
    status = data.get("status")
    if status == "ZERO_RESULTS":
        raise ValidationError("No location found for that address")
    if status != "OK" or not data.get("results"):
        msg = data.get("error_message") or f"Geocoding failed ({status})"
        raise ValidationError(msg)
    first = data["results"][0]
    loc = first.get("geometry", {}).get("location") or {}
    lat = loc.get("lat")
    lng = loc.get("lng")
    if lat is None or lng is None:
        raise ValidationError("Geocoding response missing coordinates")
    comps = first.get("address_components") or []
    fields = _parse_address_components(comps)
    return {
        "latitude": float(lat),
        "longitude": float(lng),
        "formatted_address": first.get("formatted_address") or "",
        "place_id": first.get("place_id"),
        **fields,
    }


async def reverse_geocode(latitude: float, longitude: float) -> dict[str, Any]:
    data = await _get_json({"latlng": f"{latitude},{longitude}"})
    status = data.get("status")
    if status == "ZERO_RESULTS":
        raise ValidationError("No address found for that location")
    if status != "OK" or not data.get("results"):
        msg = data.get("error_message") or f"Reverse geocoding failed ({status})"
        raise ValidationError(msg)
    first = data["results"][0]
    loc = first.get("geometry", {}).get("location") or {}
    lat = loc.get("lat")
    lng = loc.get("lng")
    if lat is None or lng is None:
        raise ValidationError("Reverse geocoding response missing coordinates")
    comps = first.get("address_components") or []
    fields = _parse_address_components(comps)
    return {
        "latitude": float(lat),
        "longitude": float(lng),
        "formatted_address": first.get("formatted_address") or "",
        "place_id": first.get("place_id"),
        **fields,
    }
