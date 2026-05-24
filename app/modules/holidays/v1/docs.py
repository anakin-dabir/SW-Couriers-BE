"""OpenAPI docs snippets for Holidays v1 API."""

from typing import Any

HOLIDAYS_LIST: dict[str, Any] = {
    "summary": "List holidays",
    "description": (
        "List holidays for a given year and audience. "
        "The year filter matches the holiday start year (internal planning year derived from `start_date`). "
        "Each holiday includes both `allowed_driver_ids` and resolved `allowed_drivers` (id + name). "
        "Admin-only."
    ),
}

HOLIDAYS_CREATE: dict[str, Any] = {
    "summary": "Create holiday",
    "description": (
        "Create a new holiday configuration. "
        "Cross-year windows are supported when end_date is in the same year as start_date or in the next year. "
        "Planning year is derived from start_date (no separate year field required). "
        "Response includes both `allowed_driver_ids` and resolved `allowed_drivers` (id + name). "
        "Admin-only."
    ),
    "openapi_extra": {
        "requestBody": {
            "content": {
                "application/json": {
                    "example": {
                        "name": "Christmas Day",
                        "start_date": "2025-12-25",
                        "end_date": "2025-12-25",
                        "audience": "BOTH",
                        "allow_shifts": True,
                        "allowed_driver_ids": [
                            "00000000-0000-0000-0000-000000000123",
                            "00000000-0000-0000-0000-000000000456",
                        ],
                    }
                }
            }
        }
    },
}

HOLIDAYS_GET: dict[str, Any] = {
    "summary": "Get holiday",
    "description": "Get a single holiday by ID. Response includes `allowed_drivers` (id + name) for UI display. Admin-only.",
}

HOLIDAYS_UPDATE: dict[str, Any] = {
    "summary": "Update holiday",
    "description": (
        "Update an existing holiday. "
        "Cross-year windows are supported when end_date is in the same year as start_date or in the next year. "
        "Planning year is derived from start_date. "
        "If `allowed_driver_ids` is provided, it replaces the previous full list. "
        "If omitted, existing allowed drivers remain unchanged. "
        "Admin-only."
    ),
    "openapi_extra": {
        "requestBody": {
            "content": {
                "application/json": {
                    "example": {
                        "name": "Christmas (Observed)",
                        "start_date": "2025-12-26",
                        "end_date": "2025-12-26",
                        "audience": "INTERNAL",
                        "allow_shifts": False,
                        "allowed_driver_ids": [],
                    }
                }
            }
        }
    },
}

HOLIDAYS_DELETE: dict[str, Any] = {
    "summary": "Delete holiday",
    "description": "Delete a holiday. Admin-only.",
}

HOLIDAYS_COPY: dict[str, Any] = {
    "summary": "Copy holidays between years",
    "description": (
        "Copy all holidays from a source year to a target year. "
        "Request uses explicit `source_year` and `target_year`. "
        "Existing holidays for the target year are deleted before copying. "
        "Duration and cross-year windows are preserved (example: 2025-12-29..2026-01-01 -> 2026-12-29..2027-01-01)."
    ),
    "openapi_extra": {
        "requestBody": {
            "content": {
                "application/json": {
                    "example": {
                        "source_year": 2024,
                        "target_year": 2025,
                    }
                }
            }
        }
    },
}

HOLIDAYS_YEARS: dict[str, Any] = {
    "summary": "List holiday years",
    "description": "List configured holiday years with number of holidays in each year, sorted newest first. Admin-only.",
    "openapi_extra": {
        "responses": {
            200: {
                "content": {
                    "application/json": {
                        "example": {
                            "success": True,
                            "data": {
                                "items": [
                                    {"year": 2027, "holidays_count": 8},
                                    {"year": 2026, "holidays_count": 10},
                                    {"year": 2025, "holidays_count": 9},
                                ],
                                "total": 3,
                            },
                        }
                    }
                }
            }
        }
    },
}
