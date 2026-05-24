"""Central API router. Mounts all module routers under versioned prefixes.

Add new module routers here as they're implemented.
"""

from enum import Enum
from typing import cast

from fastapi import APIRouter
from starlette.responses import JSONResponse

from app.common.types import VersionedModulesList
from app.modules.account_statements.v1.org_routes import router as account_statements_org_v1_router
from app.modules.admins.v1.routes import router as admins_v1_router
from app.modules.audit.v1.routes import router as audit_v1_router
from app.modules.auth.v1.driver_activation_routes import router as driver_activation_v1_router
from app.modules.auth.v1.routes import router as auth_v1_router
from app.modules.auth.v1.routes import invite_router as invite_v1_router
from app.modules.orders.v1.routes import router as orders_v1_router
from app.modules.billing.v1.routes import router as billing_v1_router
from app.modules.billing.v1.org_overview_routes import router as billing_org_overview_v1_router
from app.modules.dashboard.v1.routes import router as dashboard_v1_router

from app.modules.drivers.v1.routes import router as drivers_v1_router
from app.modules.dropdown_configs.v1.routes import router as dropdown_configs_v1_router
from app.modules.drivers.v1.self_routes import router as drivers_self_v1_router
from app.modules.drivers.v1.stop_execution_routes import router as drivers_stop_exec_v1_router
from app.modules.holidays.v1.routes import router as holidays_v1_router
from app.modules.invoices.v1.routes import router as invoices_v1_router
from app.modules.notifications.v1.routes import router as notification_v1_router
from app.modules.org_credit.v1.routes import router as org_credit_v1_router
from app.modules.crew.v1.routes import router as crew_v1_router
from app.modules.org_credit_alerts.v1.routes import router as org_credit_alerts_v1_router
from app.modules.org_credit_alerts.v1.routes import global_router as org_credit_alert_global_router
from app.modules.org_credit_settings.v1.routes import org_credit_settings_router
from app.modules.org_credit_applications.v1.routes import (
    credit_limit_requests_router as org_credit_limit_increase_v1_router,
    router as org_credit_applications_v1_router,
)
from app.modules.org_credit_monitoring.v1.routes import router as org_credit_monitoring_v1_router
from app.modules.org_credit_reviews.v1.routes import router as org_credit_reviews_v1_router
from app.modules.org_notes.v1.routes import notes_router as org_notes_v1_router
from app.modules.org_notes.v1.routes import tags_router as org_note_tags_v1_router
from app.modules.organizations.v1.routes import router as organization_v1_router
from app.modules.organizations.v1.shared_routes import router as shared_documents_v1_router
from app.integrations.quickbooks.routes import router as quickbooks_v1_router
from app.modules.payments.v1.routes import router as payments_v1_router
from app.modules.payments.webhooks import router as payments_webhooks_router
from app.modules.pickup_addresses.v1.routes import router as pickup_addresses_v1_router
from app.modules.planning.v1.routes import router as planning_v1_router
from app.modules.permission.v1.routes import router as permission_v1_router
from app.modules.service_tiers.v1.routes import router as service_tiers_v1_router
from app.modules.client_inactivity.v1.routes import router as client_inactivity_v1_router
from app.modules.delivery_attempts.v1.routes import router as delivery_attempts_v1_router
from app.modules.suspension_rules.v1.routes import router as suspension_rules_v1_router
from app.modules.team_availability.v1.routes import router as team_availability_v1_router
from app.modules.status_automation_rules.v1.routes import router as status_automation_rules_v1_router
from app.modules.user.v1.routes import router as user_v1_router
from app.modules.vehicle_inspections.v1.routes import router as inspections_v1_router
from app.modules.vehicles.v1.routes import router as vehicles_v1_router
from app.modules.orders.v1.routes import router as orders_v1_router

api_router = APIRouter()

# ── Versioned routers with unified docs ────────────────────────

versioned_modules: VersionedModulesList = [
    {
        "prefix": "/v1",
        "modules": [
            {
                "router": auth_v1_router,
                "prefix": "/auth",
                "tags": ["Auth (v1)"],
            },
            {
                "router": invite_v1_router,
                "prefix": "/auth",
                "tags": ["Auth Invites (v1)"],
            },
            {
                "router": driver_activation_v1_router,
                "prefix": "/auth",
                "tags": ["Auth Driver Activation (v1)"],
            },
            {
                "router": user_v1_router,
                "prefix": "/users",
                "tags": ["Users (v1)"],
            },
            {
                "router": admins_v1_router,
                "prefix": "/admins",
                "tags": ["Admins (v1)"],
            },
            {
                "router": dashboard_v1_router,
                "prefix": "/dashboard",
                "tags": ["Dashboard (v1)"],
            },
            {
                "router": permission_v1_router,
                "prefix": "/permissions",
                "tags": ["Permissions (v1)"],
            },
            {
                "router": holidays_v1_router,
                "prefix": "/holidays",
                "tags": ["Holidays (v1)"],
            },
            {
                "router": team_availability_v1_router,
                "prefix": "/team-availability",
                "tags": ["Team Availability (v1)"],
            },
            {
                "router": invoices_v1_router,
                "prefix": "/invoices",
                "tags": ["Invoices (v1)"],
            },
            {
                "router": orders_v1_router,
                "prefix": "/orders",
                "tags": ["Orders (v1)"],
            },
            {
                "router": pickup_addresses_v1_router,
                "prefix": "/pickup-addresses",
                "tags": ["Pickup addresses (v1)"],
            },
            {
                "router": quickbooks_v1_router,
                "prefix": "/integrations/quickbooks",
                "tags": ["QuickBooks (v1)"],
            },
            {
                "router": billing_v1_router,
                "prefix": "/billing",
                "tags": ["Billing (v1)"],
            },
            {
                "router": service_tiers_v1_router,
                "prefix": "/service-tiers",
                "tags": ["Service Tiers (v1)"],
            },
            {
                "router": client_inactivity_v1_router,
                "prefix": "/client-inactivity-config",
                "tags": ["Client Inactivity (v1)"],
            },
            {
                "router": delivery_attempts_v1_router,
                "prefix": "/delivery-attempts",
                "tags": ["Delivery Attempts (v1)"],
            },
            {
                "router": dropdown_configs_v1_router,
                "prefix": "/dropdown-configs",
                "tags": ["Dropdown configs (v1)"],
            },
            {
                "router": suspension_rules_v1_router,
                "prefix": "/suspension-rules",
                "tags": ["Suspension Rules (v1)"],
            },
            {
                "router": status_automation_rules_v1_router,
                "prefix": "/status-automation-rules",
                "tags": ["Status Automation Rules (v1)"],
            },
            {
                "router": vehicles_v1_router,
                "prefix": "/vehicles",
                "tags": ["Vehicles (v1)"],
            },
            {
                "router": inspections_v1_router,
                "prefix": "/vehicle-inspections",
                "tags": ["Vehicle Inspections (v1)"],
            },
            {
                "router": organization_v1_router,
                "prefix": "/organizations",
                "tags": ["Organizations (v1)"],
            },
            {
                "router": account_statements_org_v1_router,
                "prefix": "/organizations",
                "tags": ["Account Statements (v1)"],
            },
            {
                "router": billing_org_overview_v1_router,
                "prefix": "/organizations",
                "tags": ["Billing Overview (v1)"],
            },
            {
                "router": audit_v1_router,
                "prefix": "/organizations",
                "tags": ["Organization Audit Logs(v1)"],
            },
            {
                "router": org_notes_v1_router,
                "prefix": "/organizations",
                "tags": ["Organizations (v1)"],
            },
            {
                "router": org_credit_applications_v1_router,
                "prefix": "/organizations",
                "tags": ["Credit Applications (v1)"],
            },
            {
                "router": org_credit_limit_increase_v1_router,
                "prefix": "/organizations",
                "tags": ["Organization Credit Limit Increase Requests (v1)"],
            },
            {
                "router": org_credit_settings_router,
                "prefix": "/organizations",
                "tags": ["Credit Settings (v1)"],
            },
            {
                "router": org_credit_v1_router,
                "prefix": "/organizations",
                "tags": ["Credit (v1)"],
            },
            {
                "router": org_credit_reviews_v1_router,
                "prefix": "/organizations",
                "tags": ["Credit Reviews (v1)"],
            },
            {
                "router": org_credit_monitoring_v1_router,
                "prefix": "/organizations",
                "tags": ["Credit Monitoring (v1)"],
            },
            {
                "router": org_credit_alerts_v1_router,
                "prefix": "/organizations",
                "tags": ["Credit Alerts (v1)"],
            },
            {
                "router": org_credit_alert_global_router,
                "prefix": "/credit/alerts",
                "tags": ["Credit Alerts Global Settings (v1)"],
            },
            {
                "router": crew_v1_router,
                "prefix": "/crews",
                "tags": ["Crews (v1)"],
            },
            {
                "router": shared_documents_v1_router,
                "prefix": "/shared/documents",
                "tags": ["Organizations (v1)"],
            },
            {
                "router": org_note_tags_v1_router,
                "prefix": "/org-note-tags",
                "tags": ["Organizations (v1)"],
            },
            {
                "router": drivers_v1_router,
                "prefix": "/drivers",
                "tags": ["Drivers (v1)"],
            },
            {
                "router": drivers_self_v1_router,
                "prefix": "/driver-profile",
                "tags": ["Drivers Self (v1)"],
            },
            {
                "router": drivers_stop_exec_v1_router,
                "prefix": "/driver-profile",
                "tags": ["Drivers Self (v1)"],
            },
            {
                "router": planning_v1_router,
                "prefix": "/routes",
                "tags": ["Planning / Routes (v1)"],
            },
            {
                "router": notification_v1_router,
                "prefix": "/notifications",
                "tags": ["Notifications (v1)"],
            },
            {
                "router": payments_v1_router,
                "prefix": "/payment-methods",
                "tags": ["Payment methods (v1)"],
            },
        ],
    },
]

for vcfg in versioned_modules:
    version_router = APIRouter(prefix=vcfg["prefix"])
    for mod in vcfg["modules"]:
        version_router.include_router(
            mod["router"],
            prefix=mod.get("prefix", ""),
            tags=cast(list[str | Enum], mod.get("tags", [])),
        )
    api_router.include_router(version_router)

api_router.include_router(
    payments_webhooks_router,
    prefix="/v1/webhooks/payments",
    tags=["Payment Webhooks"],
)


# ── Health checks (no auth) ───────────────────


@api_router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Basic liveness. For readiness use /health/db and /health/redis."""
    return {"status": "ok"}


@api_router.get("/health/db", tags=["system"])
async def health_db() -> JSONResponse:
    """Verify database connectivity. Returns 503 if DB is unreachable."""
    from sqlalchemy import text

    from app.core.database import _get_engine

    try:
        engine = _get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return JSONResponse(content={"status": "ok"})
    except Exception:
        return JSONResponse(content={"status": "unhealthy"}, status_code=503)


@api_router.get("/health/redis", tags=["system"])
async def health_redis() -> JSONResponse:
    """Verify Redis connectivity. Returns 503 if Redis is unreachable or not initialized."""
    try:
        from app.core.redis import get_redis

        redis = get_redis()
        await redis.ping()
        return JSONResponse(content={"status": "ok"})
    except Exception:
        return JSONResponse(content={"status": "unhealthy"}, status_code=503)
