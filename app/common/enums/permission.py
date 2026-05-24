"""Permission enums — hardcoded resources and access levels.

Resources represent top-level business objects in the system.
PermissionLevel uses IntEnum so comparisons work naturally:
WRITE > READ > NONE.
"""

import enum


class Resource(enum.StrEnum):
    """Business resources that can be permission-gated.

    Add new entries as modules are built. Existing values must never
    be renamed (they are stored in the user_permissions table).
    """

    # ── Internal admin resources ──────────────────────────────────────────────
    DASHBOARD = "DASHBOARD"
    SHIPMENTS = "SHIPMENTS"
    WAREHOUSES = "WAREHOUSES"
    DRIVERS = "DRIVERS"
    CUSTOMERS = "CUSTOMERS"
    ORGANIZATIONS = "ORGANIZATIONS"
    INVOICES = "INVOICES"  # Deprecated: admin finance uses BILLING (see migration 0143).
    PAYMENTS = "PAYMENTS"
    REPORTS = "REPORTS"
    REGIONS = "REGIONS"
    USERS = "USERS"
    ADMINS = "ADMINS"
    RESET_ADMIN_PASSWORDS = "RESET_ADMIN_PASSWORDS"
    RESET_B2B_CLIENT_PASSWORDS = "RESET_B2B_CLIENT_PASSWORDS"
    AUDIT_LOG = "AUDIT_LOG"
    SETTINGS = "SETTINGS"
    SUPPORT_TICKETS = "SUPPORT_TICKETS"
    VEHICLE_MANAGEMENT = "VEHICLE_MANAGEMENT"
    QUICKBOOKS = "QUICKBOOKS"
    CREDIT_NOTES = "CREDIT_NOTES"
    ACCOUNT_STATEMENTS = "ACCOUNT_STATEMENTS"  # Deprecated: routes use BILLING.
    ROUTE_PLANNING = "ROUTE_PLANNING"

    # ── System configuration resources ───────────────────────────────────────
    HOLIDAYS = "HOLIDAYS"
    SUSPENSION_RULES = "SUSPENSION_RULES"
    STATUS_AUTOMATION_RULES = "STATUS_AUTOMATION_RULES"  # deprecated: use SYSTEM_DEFAULTS in routes/UI
    SYSTEM_DEFAULTS = "SYSTEM_DEFAULTS"
    SERVICE_TIERS = "SERVICE_TIERS"
    DYNAMIC_CONFIGS = "DYNAMIC_CONFIGS"

    # ── Logs & records ────────────────────────────────────────────────────────
    ACCESS_LOGS = "ACCESS_LOGS"
    DOCUMENTS = "DOCUMENTS"

    # ── B2B customer portal resources (Figma nav tabs) ───────────────────────
    ORDERS = "ORDERS"  # Orders / Bookings tab (B2B portal)
    REQUESTS = "REQUESTS"  # Deprecated: legacy name for ORDERS; kept until DB rows are migrated.
    BILLING = "BILLING"  # Finance: invoices, payments, account statements, org billing overview
    NOTIFICATIONS = "NOTIFICATIONS"  # Notifications tab
    CONTACTS = "CONTACTS"  # Org contacts tab (manage secondary contacts)
    ORG_PROFILE = "ORG_PROFILE"  # Company profile, logo, pickups, notification prefs (delegate via override)
    CARD_PAYMENT = "CARD_PAYMENT"  # Card payment access in the B2B portal
    REQUEST_CREDIT = "REQUEST_CREDIT"  # Submit a credit account request from the portal
    REPORTING = "REPORTING"  # Bookings and financial reports in the portal
    BILLING_REFUNDS = "BILLING_REFUNDS"  # Refund management in admin portal


class PermissionLevel(enum.IntEnum):
    """Access level for a resource. Higher value implies all lower levels.

    WRITE (2) implies READ (1). Use ``>=`` for checks:
        if level >= PermissionLevel.READ: ...
    """

    NONE = 0
    READ = 1
    WRITE = 2
