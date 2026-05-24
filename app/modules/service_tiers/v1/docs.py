"""OpenAPI docs snippets for Service Tiers v1 API."""

from typing import Any

from app.modules.service_tiers.constants import SUPERFAST_TIER_NAME

_SYSTEM_TIER_NOTE = (
    f"**System tier ({SUPERFAST_TIER_NAME}):** seeded by migration; always present in the global catalog. "
    "Its name, audience, and lifecycle cannot be changed or deleted via the API. "
    "Pricing, duration, description, color, and icon may be updated globally. "
    "For each organisation it is always **permitted** (cannot be deselected); "
    "per-client custom pricing uses `plain_type: custom` on organisation pricing plans."
)

SERVICE_TIERS_LIST: dict[str, Any] = {
    "summary": "List service tiers",
    "description": (
        "List configured service tiers (GLOBAL and/or ORG rows). Filter by scope_type and scope_org_id. "
        "Admin-only.\n\n" + _SYSTEM_TIER_NOTE
    ),
}

SERVICE_TIERS_EFFECTIVE_LIST: dict[str, Any] = {
    "summary": "List effective service tiers for an organisation",
    "description": (
        "Resolves GLOBAL defaults with ORG overrides by (tier_name, available_for), "
        "plus org-only tiers. Enriches rows with contract state: `permitted`, `is_default`, `plain_type`. "
        "System tier Superfast is always returned with `permitted: true` and `permitted_locked: true`. "
        "Same merge pattern as suspension effective rule sets. Admin or B2B read."
    ),
}

SERVICE_TIERS_ORG_OVERRIDE_UPSERT: dict[str, Any] = {
    "summary": "Upsert organisation service tier override",
    "description": (
        "Create or update an ORG-scoped tier for the given tier_name and available_for. "
        "When a matching global tier exists, omitted fields default from that global row. "
        "Use this for per-organisation custom Superfast pricing. Admin-only."
    ),
}

SERVICE_TIERS_CREATE: dict[str, Any] = {
    "summary": "Create service tier",
    "description": (
        "Create a new service tier configuration. Admin-only.\n\n"
        "**Scope:** `scope_type` defaults to `GLOBAL`. **Validation:** When `scope_type` is `GLOBAL`, "
        "`scope_org_id` must be omitted or `null` (a non-null value is rejected). "
        "When `scope_type` is `ORG`, `scope_org_id` is **required** and must be the target organisation id.\n\n"
        f"Creating a GLOBAL tier named `{SUPERFAST_TIER_NAME}` is rejected (system tier)."
    ),
    "openapi_extra": {
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "global_tier": {
                            "summary": "Global tier",
                            "description": "Default platform-wide tier; do not set scope_org_id.",
                            "value": {
                                "tier_name": "Basic",
                                "duration_days": 30,
                                "error_margin_kg": 12,
                                "price_per_kg": 2.5,
                                "price_per_package": 9.99,
                                "base_price": 5.0,
                                "available_for": "BOTH",
                                "scope_type": "GLOBAL",
                                "color": "#FFAA00",
                                "icon": "box",
                            },
                        },
                        "org_tier": {
                            "summary": "Organisation-scoped tier",
                            "description": "Tier only for one organisation; scope_org_id is required when scope_type is ORG.",
                            "value": {
                                "tier_name": "Basic",
                                "duration_days": 30,
                                "error_margin_kg": 12,
                                "price_per_kg": 2.5,
                                "price_per_package": 9.99,
                                "base_price": 5.0,
                                "available_for": "BOTH",
                                "scope_type": "ORG",
                                "scope_org_id": "550e8400-e29b-41d4-a716-446655440000",
                                "color": "#FFAA00",
                                "icon": "box",
                            },
                        },
                    }
                }
            }
        }
    },
}

SERVICE_TIERS_GET: dict[str, Any] = {
    "summary": "Get service tier",
    "description": (
        "Get a single service tier by ID. Admin-only.\n\n"
        "System tier responses include `is_system_tier: true` and `tier_name_locked: true`."
    ),
}

SERVICE_TIERS_UPDATE: dict[str, Any] = {
    "summary": "Update service tier",
    "description": (
        "Update an existing service tier. Admin-only.\n\n"
        f"For `{SUPERFAST_TIER_NAME}`: `tier_name`, `available_for`, and `status` changes are rejected; "
        "price, duration, description, color, and icon may be updated."
    ),
    "openapi_extra": {
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "standard_update": {
                            "summary": "Update pricing and presentation",
                            "value": {
                                "price_per_package": 12.50,
                                "color": "#00AAFF",
                                "icon": "star",
                                "version": 1,
                            },
                        },
                        "superfast_update": {
                            "summary": "Update global Superfast pricing",
                            "description": "Name and status cannot be changed on the system tier.",
                            "value": {
                                "price_per_package": 130.0,
                                "duration_days": 1,
                                "description": "Express delivery tier",
                                "version": 1,
                            },
                        },
                    }
                }
            }
        }
    },
}

SERVICE_TIERS_DELETE: dict[str, Any] = {
    "summary": "Delete service tier",
    "description": (
        "Delete a service tier. Admin-only.\n\n"
        f"Deleting the system tier `{SUPERFAST_TIER_NAME}` is rejected."
    ),
}

SERVICE_TIERS_GLOBAL_LIST: dict[str, Any] = {
    "summary": "List GLOBAL service tier catalog",
    "description": (
        "Platform-wide tiers only (same as GET /service-tiers with scope_type=GLOBAL). "
        "Always includes the system tier Superfast. Admin-only.\n\n" + _SYSTEM_TIER_NOTE
    ),
}
