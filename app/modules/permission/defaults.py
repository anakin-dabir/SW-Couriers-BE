"""Default permission matrix — hardcoded, no DB.

Each role gets a sensible baseline. Admin can override per user via the
user_permissions table. The PermissionService merges defaults + overrides
at resolution time.

To add a new resource:
  1. Add it to Resource enum (app/common/enums/permission.py)
  2. Add a row to EVERY role dict below
  3. Run tests — the test_all_resources_covered guard will catch missing entries
"""

from app.common.enums.permission import PermissionLevel, Resource
from app.common.enums.user import UserRole

N = PermissionLevel.NONE
R = PermissionLevel.READ
W = PermissionLevel.WRITE

# Admin & super-admin defaults — these dicts only list the resources surfaced
# on the admin permission matrix UI (see frontend ADMIN_PERMISSION_MATRIX).
# Any resource NOT listed here falls through to ``NONE`` via ``get_role_defaults``.
_ADMIN_MATRIX_LEVELS: dict[Resource, PermissionLevel] = {
    # Operations
    Resource.VEHICLE_MANAGEMENT: W,
    Resource.DRIVERS: W,
    # Clients & Orders
    Resource.ORGANIZATIONS: W,
    Resource.CUSTOMERS: W,
    Resource.RESET_B2B_CLIENT_PASSWORDS: W,
    # Admin Control
    Resource.ADMINS: W,
    Resource.CREDIT_NOTES: W,
    Resource.ROUTE_PLANNING: W,
    Resource.RESET_ADMIN_PASSWORDS: W,
    # System Configurations
    Resource.HOLIDAYS: W,
    Resource.SUSPENSION_RULES: W,
    Resource.SYSTEM_DEFAULTS: W,
    Resource.SERVICE_TIERS: W,
    Resource.NOTIFICATIONS: W,
    Resource.QUICKBOOKS: W,
    # Monitoring & Finance (payments, invoices, refunds gate on BILLING)
    Resource.DASHBOARD: W,
    Resource.BILLING: W,
    # Logs & Records
    Resource.ACCESS_LOGS: R,
    Resource.AUDIT_LOG: R,
    Resource.DOCUMENTS: W,
}

# Read-only dropdown access; write is bundled under SYSTEM_DEFAULTS at check time.
_ADMIN_ROLE_LEVELS: dict[Resource, PermissionLevel] = {
    **_ADMIN_MATRIX_LEVELS,
    Resource.DYNAMIC_CONFIGS: R,
}


DEFAULT_PERMISSIONS: dict[UserRole, dict[Resource, PermissionLevel]] = {
    # Super-admin gets WRITE on everything in the admin matrix, including logs.
    UserRole.SUPER_ADMIN: {
        **_ADMIN_ROLE_LEVELS,
        Resource.ACCESS_LOGS: W,
        Resource.AUDIT_LOG: W,
    },
    UserRole.ADMIN: _ADMIN_ROLE_LEVELS,
    UserRole.WAREHOUSE_STAFF: {
        Resource.DASHBOARD: R,
        Resource.SHIPMENTS: W,
        Resource.WAREHOUSES: R,
        Resource.DRIVERS: N,
        Resource.CUSTOMERS: N,
        Resource.ORGANIZATIONS: N,
        Resource.INVOICES: N,
        Resource.PAYMENTS: N,
        Resource.REPORTS: R,
        Resource.REGIONS: N,
        Resource.USERS: N,
        Resource.ADMINS: N,
        Resource.RESET_ADMIN_PASSWORDS: N,
        Resource.RESET_B2B_CLIENT_PASSWORDS: N,
        Resource.AUDIT_LOG: R,
        Resource.SETTINGS: N,
        Resource.SUPPORT_TICKETS: R,
        Resource.VEHICLE_MANAGEMENT: N,
        Resource.QUICKBOOKS: N,
        Resource.CREDIT_NOTES: N,
        Resource.ROUTE_PLANNING: R,
        Resource.HOLIDAYS: N,
        Resource.SUSPENSION_RULES: N,
        Resource.STATUS_AUTOMATION_RULES: N,
        Resource.SYSTEM_DEFAULTS: N,
        Resource.SERVICE_TIERS: N,
        Resource.DYNAMIC_CONFIGS: N,
        Resource.ACCESS_LOGS: N,
        Resource.DOCUMENTS: N,
        Resource.REQUESTS: N,
        Resource.BILLING: N,
        Resource.NOTIFICATIONS: N,
        Resource.CONTACTS: N,
        Resource.ORG_PROFILE: N,
        Resource.CARD_PAYMENT: N,
        Resource.REQUEST_CREDIT: N,
        Resource.REPORTING: N,
        Resource.BILLING_REFUNDS: N,
    },
    UserRole.DRIVER: {
        Resource.DASHBOARD: N,
        Resource.SHIPMENTS: R,
        Resource.WAREHOUSES: N,
        Resource.DRIVERS: R,
        Resource.CUSTOMERS: N,
        Resource.ORGANIZATIONS: N,
        Resource.INVOICES: N,
        Resource.PAYMENTS: N,
        Resource.REPORTS: N,
        Resource.REGIONS: N,
        Resource.USERS: N,
        Resource.ADMINS: N,
        Resource.RESET_ADMIN_PASSWORDS: N,
        Resource.RESET_B2B_CLIENT_PASSWORDS: N,
        Resource.AUDIT_LOG: N,
        Resource.SETTINGS: N,
        Resource.SUPPORT_TICKETS: R,
        Resource.VEHICLE_MANAGEMENT: N,
        Resource.QUICKBOOKS: N,
        Resource.CREDIT_NOTES: N,
        Resource.ROUTE_PLANNING: R,
        Resource.HOLIDAYS: N,
        Resource.SUSPENSION_RULES: N,
        Resource.STATUS_AUTOMATION_RULES: N,
        Resource.SYSTEM_DEFAULTS: N,
        Resource.SERVICE_TIERS: N,
        Resource.DYNAMIC_CONFIGS: N,
        Resource.ACCESS_LOGS: N,
        Resource.DOCUMENTS: N,
        Resource.REQUESTS: N,
        Resource.BILLING: N,
        Resource.NOTIFICATIONS: N,
        Resource.CONTACTS: N,
        Resource.ORG_PROFILE: N,
        Resource.CARD_PAYMENT: N,
        Resource.REQUEST_CREDIT: N,
        Resource.REPORTING: N,
        Resource.BILLING_REFUNDS: N,
    },
    # B2B portal — only the resources surfaced in the B2B navigation matrix.
    # Anything not listed here falls through to NONE via ``get_role_defaults``.
    UserRole.CUSTOMER_B2B: {
        Resource.DASHBOARD: R,
        Resource.ORDERS: W,
        Resource.CARD_PAYMENT: W,
        Resource.BILLING: R,
        Resource.NOTIFICATIONS: R,
        Resource.REQUEST_CREDIT: W,
        Resource.DOCUMENTS: R,
        Resource.CONTACTS: W,
        Resource.ORG_PROFILE: R,
        Resource.AUDIT_LOG: R,
        # Org-scoped status automation API (not shown on B2B portal permission matrix).
        Resource.SYSTEM_DEFAULTS: W,
    },
    UserRole.CUSTOMER_B2C: {
        Resource.DASHBOARD: R,
        Resource.SHIPMENTS: R,
        Resource.WAREHOUSES: N,
        Resource.DRIVERS: N,
        Resource.CUSTOMERS: N,
        Resource.ORGANIZATIONS: N,
        Resource.INVOICES: R,
        Resource.PAYMENTS: R,
        Resource.REPORTS: N,
        Resource.REGIONS: N,
        Resource.USERS: N,
        Resource.ADMINS: N,
        Resource.RESET_ADMIN_PASSWORDS: N,
        Resource.RESET_B2B_CLIENT_PASSWORDS: N,
        Resource.AUDIT_LOG: N,
        Resource.SETTINGS: N,
        Resource.SUPPORT_TICKETS: W,
        Resource.VEHICLE_MANAGEMENT: N,
        Resource.QUICKBOOKS: N,
        Resource.CREDIT_NOTES: N,
        Resource.ROUTE_PLANNING: N,
        Resource.HOLIDAYS: N,
        Resource.SUSPENSION_RULES: N,
        Resource.STATUS_AUTOMATION_RULES: N,
        Resource.SYSTEM_DEFAULTS: N,
        Resource.SERVICE_TIERS: N,
        Resource.DYNAMIC_CONFIGS: N,
        Resource.ACCESS_LOGS: N,
        Resource.DOCUMENTS: N,
        Resource.REQUESTS: W,
        Resource.BILLING: R,
        Resource.NOTIFICATIONS: R,
        Resource.CONTACTS: N,
        Resource.ORG_PROFILE: N,
        Resource.CARD_PAYMENT: N,
        Resource.REQUEST_CREDIT: N,
        Resource.REPORTING: N,
        Resource.BILLING_REFUNDS: N,
    },
}


def get_role_defaults(role: UserRole) -> dict[Resource, PermissionLevel]:
    """Return the default permission set for a role — every resource always present.

    Role dicts in :data:`DEFAULT_PERMISSIONS` may be partial (admin and
    super-admin intentionally list only the resources on the admin UI matrix).
    Any resource missing from a role's dict is treated as
    :attr:`PermissionLevel.NONE`. An unknown role gets all-NONE.
    """
    base = DEFAULT_PERMISSIONS.get(role, {})
    return {resource: base.get(resource, PermissionLevel.NONE) for resource in Resource}
