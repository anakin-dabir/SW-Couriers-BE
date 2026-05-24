"""OpenAPI docs snippets for Crew v1 API."""

from typing import Any

CREWS_ELIGIBLE_DRIVERS: dict[str, Any] = {
    "summary": "List drivers eligible for a new crew",
    "description": (
        "Paginated list of users with role `DRIVER` and status `ACTIVE` that "
        "are **not** currently in an open crew. Use as the dropdown source "
        "when opening a crew. Optional `search` matches first name, last name, "
        "or email (case-insensitive partial)."
    ),
}

CREWS_ELIGIBLE_ROUTES: dict[str, Any] = {
    "summary": "List routes eligible for a crew assignment",
    "description": (
        "Paginated list of routes that are not `COMPLETED` and have **no** "
        "currently open crew assignment. Use as the dropdown source when "
        "assigning a crew to a route. Optional `search` matches `route_code` "
        "(case-insensitive partial)."
    ),
}
