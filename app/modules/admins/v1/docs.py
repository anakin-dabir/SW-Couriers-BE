from __future__ import annotations

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry

CREATE_ADMIN = create_doc_entry(
    "Create a new admin user with permissions",
    {
        201: success_entry(
            "Admin created",
            data={
                "user_id": "00000000-0000-0000-0000-000000000000",
                "email": "admin@example.com",
                "invite_id": "00000000-0000-0000-0000-000000000001",
                "status": "PENDING_VERIFICATION",
            },
            message="Admin created and invite email is being sent.",
        ),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry("Not allowed (admin only)", code="FORBIDDEN", message="This action requires one of: Admin"),
        409: error_entry("Email already registered", code="CONFLICT", message="Email already registered"),
    },
    description=(
        "Implements the **Create New Admin** wizard. "
        "Send as **`multipart/form-data`** to support the optional profile photo.\n\n"
        "**Step 1 — Basic Info**\n\n"
        "| Field | Type | Required | Notes |\n"
        "|---|---|---|---|\n"
        "| `first_name` | string | **Yes** | 1–100 chars |\n"
        "| `last_name` | string | **Yes** | 1–100 chars |\n"
        "| `email` | string | **Yes** | Must be unique |\n"
        "| `title` | string | No | `MR` `MRS` `MS` `DR` `PROF` |\n"
        "| `phone` | string | No | Up to 50 chars |\n"
        "| `position_role` | string | No | Free-text job title, e.g. *Operations Manager* |\n"
        "| `address_line_1` | string | **Yes** | 1–255 chars |\n"
        "| `address_line_2` | string | No | 1–255 chars |\n"
        "| `city` | string | **Yes** | 1–100 chars |\n"
        "| `state` | string | **Yes** | 1–100 chars |\n"
        "| `postcode` | string | **Yes** | 1–20 chars |\n"
        "| `country` | string | No | Defaults to **United Kingdom** when omitted |\n"
        "| `profile_photo` | file | No | JPEG or PNG, max 5 MB |\n\n"
        "**Step 2 — Permissions**\n\n"
        "`permissions` is a **JSON string** (form field) containing an array of `{resource, level}` overrides. "
        "Resources not listed fall back to the ADMIN role defaults "
        "(WRITE for most, READ for AUDIT_LOG/ACCESS_LOGS, NONE for B2B portal resources).\n\n"
        "Valid resource names match the ``Resource`` enum (including ``RESET_ADMIN_PASSWORDS`` and "
        "``RESET_B2B_CLIENT_PASSWORDS`` for admin-initiated password resets on staff vs B2B portal users).\n\n"
        "*Admin resources:* `DASHBOARD` `SHIPMENTS` `WAREHOUSES` `DRIVERS` `CUSTOMERS` "
        "`ORGANIZATIONS` `INVOICES` `PAYMENTS` `REPORTS` `REGIONS` `USERS` `ADMINS` `RESET_ADMIN_PASSWORDS` "
        "`RESET_B2B_CLIENT_PASSWORDS` `AUDIT_LOG` `SETTINGS` `SUPPORT_TICKETS` `VEHICLE_MANAGEMENT` `QUICKBOOKS` "
        "`ROUTE_PLANNING`\n\n"
        "*System config:* `HOLIDAYS` `SUSPENSION_RULES` `SYSTEM_DEFAULTS` (includes status automation rules "
        "and dropdown configuration write) `SERVICE_TIERS` `DYNAMIC_CONFIGS` (read-only for vehicle dropdowns)\n\n"
        "*Logs & records:* `ACCESS_LOGS` `DOCUMENTS`\n\n"
        "*Billing (admin):* `BILLING` `BILLING_REFUNDS` `CREDIT_NOTES` "
        "(payments and payment KPIs use `BILLING`, not a separate `PAYMENTS` toggle)\n\n"
        "*B2B portal (customer-facing, typically NONE for admins):* "
        "`REQUESTS` `BILLING` `NOTIFICATIONS` `CONTACTS` `ORG_PROFILE` `CARD_PAYMENT` `REQUEST_CREDIT` `REPORTING`\n\n"
        "Valid levels: `NONE` `READ` `WRITE`\n\n"
        "**Step 3 — Review & Create**\n\n"
        "- `send_invite=true` → admin created + invite email sent immediately (**Save & Create**).\n"
        "- `send_invite=false` → admin saved as draft; "
        "call `POST /admins/{user_id}/invite` when ready to send.\n\n"
        "**Example `permissions` field value (one row per resource key, as a JSON string):**\n"
        "```json\n"
        "[\n"
        '  { "resource": "DASHBOARD", "level": "WRITE" },\n'
        '  { "resource": "SHIPMENTS", "level": "WRITE" },\n'
        '  { "resource": "WAREHOUSES", "level": "WRITE" },\n'
        '  { "resource": "DRIVERS", "level": "WRITE" },\n'
        '  { "resource": "CUSTOMERS", "level": "WRITE" },\n'
        '  { "resource": "ORGANIZATIONS", "level": "WRITE" },\n'
        '  { "resource": "INVOICES", "level": "WRITE" },\n'
        '  { "resource": "REPORTS", "level": "WRITE" },\n'
        '  { "resource": "REGIONS", "level": "WRITE" },\n'
        '  { "resource": "USERS", "level": "WRITE" },\n'
        '  { "resource": "ADMINS", "level": "WRITE" },\n'
        '  { "resource": "RESET_ADMIN_PASSWORDS", "level": "WRITE" },\n'
        '  { "resource": "RESET_B2B_CLIENT_PASSWORDS", "level": "WRITE" },\n'
        '  { "resource": "AUDIT_LOG", "level": "READ" },\n'
        '  { "resource": "SETTINGS", "level": "WRITE" },\n'
        '  { "resource": "SUPPORT_TICKETS", "level": "WRITE" },\n'
        '  { "resource": "VEHICLE_MANAGEMENT", "level": "WRITE" },\n'
        '  { "resource": "QUICKBOOKS", "level": "WRITE" },\n'
        '  { "resource": "HOLIDAYS", "level": "WRITE" },\n'
        '  { "resource": "SUSPENSION_RULES", "level": "WRITE" },\n'
        '  { "resource": "SYSTEM_DEFAULTS", "level": "WRITE" },\n'
        '  { "resource": "SERVICE_TIERS", "level": "WRITE" },\n'
        '  { "resource": "DYNAMIC_CONFIGS", "level": "READ" },\n'
        '  { "resource": "ACCESS_LOGS", "level": "READ" },\n'
        '  { "resource": "DOCUMENTS", "level": "WRITE" },\n'
        '  { "resource": "BILLING", "level": "WRITE" },\n'
        '  { "resource": "BILLING_REFUNDS", "level": "WRITE" },\n'
        '  { "resource": "CREDIT_NOTES", "level": "WRITE" },\n'
        '  { "resource": "ROUTE_PLANNING", "level": "WRITE" },\n'
        '  { "resource": "REQUESTS", "level": "NONE" },\n'
        '  { "resource": "NOTIFICATIONS", "level": "NONE" },\n'
        '  { "resource": "CONTACTS", "level": "NONE" },\n'
        '  { "resource": "ORG_PROFILE", "level": "NONE" },\n'
        '  { "resource": "CARD_PAYMENT", "level": "NONE" },\n'
        '  { "resource": "REQUEST_CREDIT", "level": "NONE" },\n'
        '  { "resource": "REPORTING", "level": "NONE" }\n'
        "]\n"
        "```"
    ),
)

GET_ADMIN_STATS = create_doc_entry(
    "Get admin statistics",
    {
        200: success_entry(
            "Admin stats",
            data={"total": 1247, "active": 1100, "inactive": 56, "suspended": 12},
        ),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry("Not allowed", code="FORBIDDEN", message="This action requires one of: Admin"),
    },
    description="Returns total, active, inactive, and suspended admin counts.",
)

LIST_ADMINS = create_doc_entry(
    "List admin users with pagination and filters",
    {
        200: success_entry(
            "Paginated admin list",
            data={
                "items": [
                    {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "admin_ref": "ADM-0001",
                        "title": "MR",
                        "first_name": "John",
                        "last_name": "Doe",
                        "full_name": "John Doe",
                        "email": "admin@example.com",
                        "phone": "+44 7911 123456",
                        "position_role": "Operations Manager",
                        "address_line_1": "10 High Street",
                        "address_line_2": None,
                        "city": "Bristol",
                        "state": "England",
                        "postcode": "BS1 5TR",
                        "country": "United Kingdom",
                        "role": "ADMIN",
                        "status": "ACTIVE",
                        "last_login": "2026-04-02T10:00:00Z",
                        "created_at": "2026-04-02T00:00:00Z",
                    }
                ],
                "total": 1247,
                "page": 1,
                "size": 20,
                "pages": 63,
                "current_url": "/v1/admins?page=1&size=20",
                "next_url": "/v1/admins?page=2&size=20",
            },
        ),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry("Not allowed", code="FORBIDDEN", message="This action requires one of: Admin"),
    },
    description=(
        "Returns a paginated list of ADMIN and SUPER_ADMIN users.\n\n"
        "**Query parameters:**\n"
        "- `search` — partial match on admin_ref, name, email, phone, position_role, "
        "address line 1, city, state, postcode, or country\n"
        "- `status` — filter by account status: `ACTIVE` `SUSPENDED` `PENDING_VERIFICATION` `INACTIVE`\n"
        "- `sort` — `newest` (default) | `oldest` | `name_asc` | `name_desc`\n"
        "- `date_from` — filter by created_at >= date (ISO format: YYYY-MM-DD)\n"
        "- `date_to` — filter by created_at <= date (ISO format: YYYY-MM-DD)\n"
        "- `page` / `size` — pagination (size max 100)"
    ),
)

SEND_ADMIN_INVITE = create_doc_entry(
    "Send invite to a draft admin",
    {
        201: success_entry(
            "Invite sent",
            data={"invite_id": "00000000-0000-0000-0000-000000000001", "email": "admin@example.com"},
            message="Invite email is being sent.",
        ),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry("Not allowed (admin only)", code="FORBIDDEN", message="This action requires one of: Admin"),
        404: error_entry("Admin user not found", code="NOT_FOUND", message="user with id '...' not found"),
        409: error_entry(
            "Admin not in pending_verification status",
            code="CONFLICT",
            message="Admin must be in PENDING_VERIFICATION status to send an invite",
        ),
    },
)

SUPPORT_ISSUE_ADMIN_PASSWORD = create_doc_entry(
    "Set support-issued password for an admin staff account",
    {
        200: success_entry(
            "Password reset",
            data={"user_id": "00000000-0000-0000-0000-000000000000", "email": "admin@example.com"},
            message="Password reset. The user was signed out of all sessions.",
        ),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry(
            "Forbidden",
            code="FORBIDDEN",
            message="Requires RESET_ADMIN_PASSWORDS at WRITE level",
        ),
        404: error_entry("Admin user not found", code="NOT_FOUND", message="user with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Weak new_password or same as current password",
        ),
    },
    description=(
        "Support flow: request body supplies ``new_password`` (validated strength). Sets the user's password, "
        "sets ``force_password_change``, invalidates all sessions, and emails the plaintext password. "
        "Requires **WRITE** on **RESET_ADMIN_PASSWORDS** (enforced on the route)."
    ),
)

GET_ADMIN = create_doc_entry(
    "Get admin user detail with permissions",
    {
        200: success_entry(
            "Admin detail",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "title": "MR",
                "first_name": "John",
                "last_name": "Doe",
                "full_name": "John Doe",
                "email": "admin@example.com",
                "phone": "+44 3465 126578",
                "position_role": "Operations Manager",
                "address_line_1": "10 High Street",
                "address_line_2": None,
                "city": "Bristol",
                "state": "England",
                "postcode": "BS1 5TR",
                "country": "United Kingdom",
                "role": "ADMIN",
                "status": "ACTIVE",
                "last_login": "2026-04-02T10:00:00Z",
                "profile_photo_url": "https://imagedelivery.net/.../public?exp=...&sig=...",
                "permissions": [
                    {"resource": "VEHICLE_MANAGEMENT", "level": "WRITE"},
                    {"resource": "DRIVERS", "level": "READ"},
                ],
                "created_at": "2026-04-02T00:00:00Z",
                "updated_at": "2026-04-02T00:00:00Z",
                "version": 1,
            },
        ),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry("Not allowed (admin only)", code="FORBIDDEN", message="This action requires one of: Admin"),
        404: error_entry("Admin not found", code="NOT_FOUND", message="admin with id '...' not found"),
    },
)

UPDATE_ADMIN = create_doc_entry(
    "Update admin profile details and optional photo",
    {
        200: success_entry(
            "Admin updated",
            message="Admin updated successfully.",
        ),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry("Not allowed", code="FORBIDDEN", message="This action requires one of: Admin"),
        404: error_entry("Admin not found", code="NOT_FOUND", message="admin with id '...' not found"),
    },
    description=(
        "Send as **`multipart/form-data`**. All fields are optional (partial update).\n\n"
        "Supported fields: `first_name`, `last_name`, `title`, `phone`, `position_role`, "
        "`address_line_1`, `address_line_2`, `city`, `state`, `postcode`, `country`, `profile_photo`.\n"
        "Empty `address_line_2` clears the second line. Blank `country` is stored as United Kingdom.\n"
        "`profile_photo` accepts JPEG/PNG up to 5 MB.\n\n"
        "Returns **message only** (no `data` body). Use `GET /admins/{user_id}` for the updated profile.\n\n"
        "Permissions are updated via `PATCH /admins/{user_id}/permissions`."
    ),
)

UPDATE_ADMIN_PERMISSIONS = create_doc_entry(
    "Replace admin permission overrides",
    {
        200: success_entry(
            "Admin permissions updated",
            message="Admin permissions updated successfully.",
        ),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry(
            "Forbidden",
            code="FORBIDDEN",
            message="Requires ADMINS at WRITE level, or cannot update your own permissions",
        ),
        404: error_entry("Admin not found", code="NOT_FOUND", message="admin with id '...' not found"),
    },
    description=(
        "Replaces all permission overrides for the target admin in a single request. "
        "You cannot replace permissions for your own user id. "
        "Returns **message only** (no `data` body). Use `GET /admins/{user_id}` for the full admin including permissions."
    ),
)

SUSPEND_ADMIN = create_doc_entry(
    "Suspend an admin account",
    {
        200: success_entry(
            "Admin suspended",
            data={"id": "00000000-0000-0000-0000-000000000000", "status": "SUSPENDED"},
            message="Admin account suspended.",
        ),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry(
            "Forbidden",
            code="FORBIDDEN",
            message="Requires ADMINS at WRITE level, or cannot perform this action on your own account",
        ),
        404: error_entry("Admin not found", code="NOT_FOUND", message="admin with id '...' not found"),
        409: error_entry("Admin not active", code="CONFLICT", message="Admin must be ACTIVE to suspend"),
    },
    description=(
        "Requires **WRITE** on **ADMINS**. You cannot suspend your own account. "
        "A `reason` is required for the audit trail."
    ),
)

REACTIVATE_ADMIN = create_doc_entry(
    "Reactivate a suspended admin account",
    {
        200: success_entry(
            "Admin reactivated",
            data={"id": "00000000-0000-0000-0000-000000000000", "status": "ACTIVE"},
            message="Admin account reactivated.",
        ),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry(
            "Forbidden",
            code="FORBIDDEN",
            message="Requires ADMINS at WRITE level, or cannot perform this action on your own account",
        ),
        404: error_entry("Admin not found", code="NOT_FOUND", message="admin with id '...' not found"),
        409: error_entry("Admin not suspended", code="CONFLICT", message="Admin must be SUSPENDED to reactivate"),
    },
    description=(
        "Requires **WRITE** on **ADMINS**. You cannot reactivate your own account. "
        "A `reason` is required for the audit trail."
    ),
)

DELETE_ADMIN = create_doc_entry(
    "Delete admin account",
    {
        200: success_entry("Admin deleted", message="Admin deleted successfully."),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry(
            "Forbidden",
            code="FORBIDDEN",
            message="Requires ADMINS at WRITE level, or cannot perform this action on your own account",
        ),
        404: error_entry("Admin not found", code="NOT_FOUND", message="admin with id '...' not found"),
    },
    description=(
        "Requires **WRITE** on **ADMINS**. You cannot delete your own account. "
        "This operation permanently removes the admin account and clears profile photo from storage."
    ),
)
