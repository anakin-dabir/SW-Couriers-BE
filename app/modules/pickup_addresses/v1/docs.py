from __future__ import annotations

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry

_EXAMPLE_TS = "2026-01-15T10:30:00.000000+00:00"

_EXAMPLE_PICKUP_ADDRESS = {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "organization_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "user_id": None,
    "label": "Main depot",
    "line_1": "1 Warehouse Way",
    "line_2": None,
    "city": "Birmingham",
    "state": "West Midlands",
    "postcode": "B1 1AA",
    "country": "United Kingdom",
    "latitude": 52.4819,
    "longitude": -1.9057,
    "is_default": True,
    "created_by_user_id": "c2d3e4f5-a6b7-8901-cdef-1234567890ab",
    "created_at": _EXAMPLE_TS,
    "updated_at": _EXAMPLE_TS,
    "version": 1,
}

_EXAMPLE_GEOCODE_RESULT = {
    "latitude": 51.5014,
    "longitude": -0.1419,
    "formatted_address": "Buckingham Palace, London SW1A 1AA, UK",
    "place_id": "ChIJtV5nSAQddkgRpw__sWQPhA",
    "line_1": "Buckingham Palace",
    "line_2": None,
    "city": "London",
    "state": None,
    "postcode": "SW1A 1AA",
    "country": "United Kingdom",
}

LIST_PICKUP_ADDRESSES = create_doc_entry(
    "List saved pickup addresses for the current account (B2B: organisation; B2C: user).",
    {
        200: success_entry(
            "Wrapped list in `data` (same shape as GET one item per element).",
            data=[_EXAMPLE_PICKUP_ADDRESS],
        ),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="B2B user must belong to an organisation"),
    },
)

GET_PICKUP_ADDRESS = create_doc_entry(
    "Get one pickup address by id (scoped to your org or user).",
    {
        200: success_entry("Single pickup address in `data`.", data=_EXAMPLE_PICKUP_ADDRESS),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Not found", code="NOT_FOUND", message="Pickup address not found"),
    },
)

CREATE_PICKUP_ADDRESS = create_doc_entry(
    "Create one or more pickup addresses. Body is a JSON array. B2B rows are stored under the JWT organisation; B2C under the authenticated user.",
    {
        201: success_entry(
            "Created — `data` is a list of saved rows (order matches the request).",
            data=[_EXAMPLE_PICKUP_ADDRESS],
            message="Pickup addresses created",
        ),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        422: error_entry("Validation", code="VALIDATION_ERROR", message="Invalid payload"),
    },
)

UPDATE_PICKUP_ADDRESS = create_doc_entry(
    "Update a pickup address (partial body; optional `latitude`/`longitude` together for map pin).",
    {
        200: success_entry("Updated row in `data`.", data=_EXAMPLE_PICKUP_ADDRESS),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Not found", code="NOT_FOUND", message="Pickup address not found"),
        422: error_entry("Validation", code="VALIDATION_ERROR", message="Invalid payload"),
    },
)

GEOCODE_ADDRESS = create_doc_entry(
    "Resolve an address to coordinates (Google Geocoding API). Use for placing or validating a map pin.",
    {
        200: success_entry(
            "`data` includes `latitude`/`longitude` for the pin plus `formatted_address` and suggested address fields.",
            data=_EXAMPLE_GEOCODE_RESULT,
        ),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        422: error_entry(
            "Validation",
            code="VALIDATION_ERROR",
            message="No results, invalid address, or Google Maps API not configured",
        ),
    },
    description=(
        "Request JSON: either **`query`** (full address string) **or** structured fields "
        "**`line_1`**, **`city`**, **`postcode`** with optional **`line_2`**, **`state`**, **`country`**."
    ),
)

DELETE_PICKUP_ADDRESS = create_doc_entry(
    "Delete a pickup address.",
    {
        200: success_entry("Deleted", message="Pickup address deleted"),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Not found", code="NOT_FOUND", message="Pickup address not found"),
    },
)
