from __future__ import annotations

from app.core.swagger import (
    create_doc_entry,
    error_401_entry,
    error_entry,
    error_validation_entry,
    success_entry,
)

_EX_VALUE_ITEM = {
    "id": "00000000-0000-4000-8000-000000000001",
    "created_at": "2026-05-08T12:00:00Z",
    "updated_at": "2026-05-08T12:00:00Z",
    "dropdown_key": "FUEL_TYPE",
    "code": "DIESEL",
    "label": "Diesel",
    "color_hex": "#5C6BC0",
}

_REPLACE_BODY_EXAMPLE = {
    "values": [
        {"label": "Diesel", "color_hex": "#5C6BC0"},
        {"label": "Petrol", "color_hex": "#26A69A"},
        {"label": "Electric", "color_hex": "#FFA726"},
    ]
}

DC_LIST_KEYS = create_doc_entry(
    "List dropdown configuration keys",
    {
        200: success_entry(
            "Keys with value counts",
            data=[
                {"key": "DEFECT_CATEGORY", "display_name": "Defect Category", "values_count": 10},
                {"key": "FUEL_TYPE", "display_name": "Fuel Type", "values_count": 4},
                {"key": "MAINTENANCE_TYPE", "display_name": "Maintenance Type", "values_count": 6},
                {"key": "SERVICE_TYPE", "display_name": "Service Type", "values_count": 4},
                {"key": "VEHICLE_AVAILABILITY", "display_name": "Vehicle Availability Status", "values_count": 3},
            ],
        ),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry(
            "Forbidden",
            code="FORBIDDEN",
            message="This action requires SUPER_ADMIN or ADMIN",
        ),
    },
    description=(
        "Returns every member of **`DropdownConfigKey`** with a human-readable **`display_name`** "
        "(from `key_display_name` in `app.modules.dropdown_configs.enums`) and how many options exist "
        "for that key in **`dropdown_values`**.\n\n"
        "**Query parameters**\n\n"
        "| Parameter | Type | Required | Notes |\n"
        "|---|---|---|---|\n"
        "| `search` | string | No | 1–120 chars; substring match on enum value or display name (case-insensitive) |\n\n"
        "Responses are ordered alphabetically by **`key`**. **`SUPER_ADMIN`** or **`ADMIN`** only."
    ),
)

DC_LIST_VALUES = create_doc_entry(
    "List values for a dropdown key",
    {
        200: success_entry(
            "Ordered option rows",
            data=[
                _EX_VALUE_ITEM,
                {
                    "id": "00000000-0000-4000-8000-000000000002",
                    "created_at": "2026-05-08T12:00:00Z",
                    "updated_at": "2026-05-08T12:00:00Z",
                    "dropdown_key": "FUEL_TYPE",
                    "code": "ELECTRIC",
                    "label": "Electric",
                    "color_hex": "#FFA726",
                },
            ],
        ),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry(
            "Forbidden",
            code="FORBIDDEN",
            message="This action requires SUPER_ADMIN or ADMIN",
        ),
        422: error_validation_entry(
            "Invalid path `key`",
            message="Request validation failed",
            field="key",
            field_message="Input should be 'FUEL_TYPE', 'DEFECT_CATEGORY', 'MAINTENANCE_TYPE', 'SERVICE_TYPE' or 'VEHICLE_AVAILABILITY'",
        ),
    },
    description=(
        "Loads all rows for **`GET /keys/{key}/values`** where **`key`** is a **`DropdownConfigKey`**:\n\n"
        "- `FUEL_TYPE`\n"
        "- `DEFECT_CATEGORY`\n"
        "- `MAINTENANCE_TYPE`\n"
        "- `SERVICE_TYPE`\n"
        "- `VEHICLE_AVAILABILITY`\n\n"
        "Rows are returned **`sorted by code`** (ascending). Each item includes server-derived **`code`**, "
        "editable **`label`**, optional **`color_hex`** (`#RRGGBB` or `#RRGGBBAA`), and timestamps. "
        "**`SUPER_ADMIN`** or **`ADMIN`** only."
    ),
)

DC_LIST_ALL_VALUES_GROUPED = create_doc_entry(
    "List all dropdown values grouped by key",
    {
        200: success_entry(
            "Grouped dropdown values",
            data=[
                {
                    "key": "FUEL_TYPE",
                    "display_name": "Fuel Type",
                    "values": [_EX_VALUE_ITEM],
                }
            ],
        ),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry(
            "Forbidden",
            code="FORBIDDEN",
            message="This action requires SUPER_ADMIN or ADMIN",
        ),
    },
    description=(
        "Returns every dropdown key once, with its full ordered value list embedded under `values`. "
        "Useful for screens that need all vehicle dropdown options in one request. "
        "Groups are ordered alphabetically by key; values inside each group are ordered by code."
    ),
)

DC_REPLACE_VALUES = {
    **create_doc_entry(
        "Replace all values for a dropdown key",
        {
            200: success_entry(
                "Full list after replace",
                data=[_EX_VALUE_ITEM],
                message="Options saved successfully",
            ),
            401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
            403: error_entry(
                "Forbidden",
                code="FORBIDDEN",
                message="This action requires SUPER_ADMIN or ADMIN",
            ),
            422: error_validation_entry(
                "Validation failed (body or label-derived code)",
                message="label produces an invalid code",
                field="values",
                field_message="label could not be converted to a stable code",
            ),
        },
        description=(
            "**`PATCH /keys/{key}/values`** — replaces **every** stored option for that key in one request. "
            "Send the complete list the UI wants to keep (same pattern as a form “save”). "
            "An empty **`values`** array clears all options for the key.\n\n"
            "**Path **`key`**** — must be a **`DropdownConfigKey`** (same five values as list-values).\n\n"
            "**JSON body**\n\n"
            "| Field | Type | Required | Notes |\n"
            "|---|---|---|---|\n"
            "| `values` | array | Yes | Each element: **`label`** (1–200 chars), **`color_hex`** optional |\n\n"
            "**`code` generation** — not sent by the client. The API derives a stable uppercase snake_case "
            "token from each **`label`** (see service rules: non-alphanumerics → `_`, `&` → ` AND `, "
            "leading digit → `X_` prefix). Pattern: `/^[A-Z][A-Z0-9_]{1,63}$/`. "
            "Duplicate labels in the **same** request get suffixes **`_2`**, **`_3`**, … as needed.\n\n"
            "**Response** — full refreshed list for the key, **`sorted by code`**; top-level **`message`** "
            "is `Options saved successfully`. Requires audit context (authenticated admin). "
            "**`SUPER_ADMIN`** or **`ADMIN`** only."
        ),
    ),
    "openapi_extra": {
        "requestBody": {
            "content": {
                "application/json": {
                    "example": _REPLACE_BODY_EXAMPLE,
                }
            }
        }
    },
}
