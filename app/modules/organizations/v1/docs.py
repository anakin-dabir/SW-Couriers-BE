from __future__ import annotations

import json

from fastapi.openapi.models import Example

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry
from app.core.swagger.utils import schema_description
from app.modules.organizations.v1.schemas import OrgProfileSavePayload

# OrgMemberDep routes: platform staff bypass org_contacts membership (see access.is_platform_admin_role).
_PLATFORM_ADMIN_ORG_ACCESS = (
    "ADMIN or SUPER_ADMIN: any org (no org_contacts row required). "
    "CUSTOMER_B2B: only their own org (membership verified via org_contacts)."
)

_ORG_DATA = {
    "id": "00000000-0000-0000-0000-000000000000",
    "reference": "SWC-ORG-00001",
    "trading_name": "Acme Logistics Ltd",
    "legal_entity_name": "Acme Logistics Limited",
    "industry": "LOGISTICS",
    "company_size": "SMALL",
    "date_of_incorporation": "2015-06-01",
    "website": "https://acme-logistics.co.uk",
    "description": None,
    "phone": "+44 7700 900000",
    "companies_house_number": "12345678",
    "eori_number": None,
    "vat_number": "GB123456789",
    "reg_address_line_1": "1 Warehouse Road",
    "reg_address_line_2": None,
    "reg_city": "Manchester",
    "reg_state": None,
    "reg_postcode": "M1 1AA",
    "reg_country": "United Kingdom",
    "trading_address_line_1": None,
    "trading_address_line_2": None,
    "trading_address_city": None,
    "trading_address_state": None,
    "trading_address_postcode": None,
    "trading_address_country": None,
    "pricing_plans": None,
    "contract_reference": None,
    "pricing_agreement_start": None,
    "pricing_agreement_end": None,
    "max_package_weight": None,
    "max_package_length": None,
    "max_package_width": None,
    "max_package_height": None,
    "min_charge_per_booking": None,
    "status": "ACTIVE",
    "notes": None,
    "logo_url": None,
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z",
    "version": 1,
}

# Richer example for GET/PATCH `/organizations/{org_id}/profile`: all `OrganizationResponse` keys visible in Swagger.
_ORG_PROFILE_RESPONSE_ORG = {
    **_ORG_DATA,
    "eori_number": "GB123456789000",
    "trading_address_line_1": "2 Trade Court",
    "trading_address_line_2": "Docklands Unit",
    "trading_address_city": "London",
    "trading_address_state": "Greater London",
    "trading_address_postcode": "E14 9WZ",
    "trading_address_country": "United Kingdom",
    "contract_title": "B2B services agreement",
    "contract_expiry_date": "2027-12-31",
    "contract_url": "https://cdn.example.com/contracts/org-contract.pdf",
    # Read-only/admin-managed fields shown for response-key visibility.
    "onboarded_by": None,
    "onboarded_by_role": None,
    "account_manager_user_id": None,
    "account_manager_name": None,
    "account_manager_email": None,
    "secondary_account_manager_user_id": None,
    "secondary_account_manager_name": None,
    "secondary_account_manager_email": None,
    "additional_account_manager_user_id": None,
    "additional_account_manager_name": None,
    "additional_account_manager_email": None,
    "logo_url": "https://imagedelivery.net/example/account-logo/public",
}

_PICKUP_ADDRESS_PROFILE_ROW = {
    "id": "00000000-0000-4000-8000-000000000010",
    "organization_id": _ORG_DATA["id"],
    "user_id": None,
    "label": "Main warehouse",
    "line_1": "Street 45",
    "line_2": "Apartment 43",
    "city": "High Street London",
    "state": None,
    "postcode": "W8 5ED",
    "country": "United Kingdom",
    "latitude": None,
    "longitude": None,
    "is_default": True,
    "created_by_user_id": None,
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z",
}

_ORG_PROFILE_PAYLOAD_OBJECT: dict = {
    "trading_name": "Acme Logistics Ltd",
    "legal_entity_name": "Acme Logistics Limited",
    "description": "Direct-to-consumer retailer specialising in sustainable home decor and lifestyle products.",
    "phone": "+44 7700 900000",
    "website": "https://acme-logistics.co.uk",
    "companies_house_number": "12345678",
    "eori_number": "GB123456789000",
    "vat_number": "GB123456789",
    "registered_address": {
        "address_line_1": "1 Warehouse Road",
        "city": "Manchester",
        "postcode": "M1 1AA",
        "country": "United Kingdom",
    },
    "trading_same_as_registered_address": False,
    "trading_address": {
        "address_line_1": "2 Trade Court",
        "city": "London",
        "postcode": "E14 9WZ",
        "country": "United Kingdom",
    },
    "pickup_addresses": [
        {
            "label": "Main warehouse",
            "same_as_registered_address": True,
            "is_default": True,
        }
    ],
}

_ORG_PROFILE_PAYLOAD_OBJECT_TRADING_SAME: dict = {
    "registered_address": {
        "address_line_1": "1 Warehouse Road",
        "city": "Manchester",
        "postcode": "M1 1AA",
    },
    "trading_same_as_registered_address": True,
}

ORG_PROFILE_PAYLOAD_OPENAPI_EXAMPLES: dict[str, Example] = {
    "full_profile": Example(
        summary="Registered + trading + pickups (JSON string for form field `payload`)",
        description=(
            "Use with optional multipart file `logo`. Nested `registered_address` / `trading_address` map to flat "
            "`reg_*` / `trading_address_*` columns in the response."
        ),
        value=json.dumps(_ORG_PROFILE_PAYLOAD_OBJECT, indent=2),
    ),
    "trading_same_as_registered": Example(
        summary="Trading address copied from registered",
        description="`trading_same_as_registered_address: true` is mutually exclusive with `trading_address`.",
        value=json.dumps(_ORG_PROFILE_PAYLOAD_OBJECT_TRADING_SAME, indent=2),
    ),
}

_ORG_PROFILE_SAVE_PAYLOAD_SCHEMA_DOC = schema_description(OrgProfileSavePayload)

_CONTACT_DATA = {
    "id": "00000000-0000-0000-0000-000000000001",
    "organization_id": "00000000-0000-0000-0000-000000000000",
    "contact_number": "+44 7700 900001",
    "contact_role": "ACCOUNT_OWNER",
    "status": "ACTIVE",
    "is_primary": True,
    "user_id": "00000000-0000-0000-0000-000000000002",
    "first_name": "Jane",
    "last_name": "Smith",
    "full_name": "Jane Smith",
    "email": "jane.smith@acme-logistics.co.uk",
    "phone": "+44 7700 900001",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z",
}

_DISCOUNT_CONFIG_DATA = {
    "id": "00000000-0000-0000-0000-000000000004",
    "organization_id": "00000000-0000-0000-0000-000000000000",
    "percentage_enabled": True,
    "percentage_value": "10.00",
    "percentage_valid_from": "2026-01-01",
    "percentage_valid_until": "2026-12-31",
    "fixed_enabled": True,
    "fixed_value": "5.00",
    "fixed_valid_from": "2026-01-01",
    "fixed_valid_until": "2026-12-31",
    "volume_enabled": True,
    "volume_tiers": [
        {"min_bookings": 1, "max_bookings": 50, "discount_pct": "0.00"},
        {"min_bookings": 51, "max_bookings": 200, "discount_pct": "5.00"},
        {"min_bookings": 201, "max_bookings": None, "discount_pct": "10.00"},
    ],
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z",
    "version": 1,
}

_PAYMENT_CONFIG_DATA = {
    "id": "00000000-0000-0000-0000-000000000003",
    "organization_id": "00000000-0000-0000-0000-000000000000",
    "vat_number": "GB123456789",
    "vat_rate": "STANDARD_20",
    "vat_treatment": "UK",
    "max_delivery_attempts": 3,
    "delivery_attempt_fees": [
        {"attempt": 1, "fee": "1.00"},
        {"attempt": 2, "fee": "3.50"},
        {"attempt": 3, "fee": "6.00"},
    ],
    "max_return_attempts": 2,
    "return_attempt_fees": [
        {"attempt": 1, "fee": "12.00"},
        {"attempt": 2, "fee": "15.00"},
    ],
    "weight_margin_kg": 0.5,
    "weight_surcharge_per_kg": "1.50",
    "payment_methods": [
        {
            "id": "00000000-0000-0000-0000-000000000011",
            "organization_id": "00000000-0000-0000-0000-000000000000",
            "payment_model": "CREDIT_ACCOUNT",
            "billing_schedule": "DAYS_AFTER_ORDER",
            "billing_day_of_month": None,
            "billing_days_after_order": 14,
            "bank_account_name": None,
            "bank_account_number": None,
            "bank_sort_code": None,
            "credit_limit": "5000.00",
            "credit_utilization_warning_pct": 80,
            "is_default": True,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "version": 1,
        }
    ],
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z",
    "version": 1,
}

# ── Organization Statistics ───────────────────────────────────────────────────

GET_ORG_STATS = create_doc_entry(
    "Get B2B client organization statistics",
    {
        200: success_entry(
            "Organization stats",
            data={"total": 1247, "active": 1100, "pending_activation": 12, "inactive": 56, "suspended": 18},
        ),
        401: error_401_entry("Not authenticated", "AUTHENTICATION_ERROR", "Missing authorization header"),
        403: error_entry("Not allowed", code="FORBIDDEN", message="This action requires one of: Admin"),
    },
    description=(
        "Returns organization statistics across all B2B clients:\n\n"
        "- **total**: All organizations (active + inactive + suspended + on_hold)\n"
        "- **active**: Organizations with status = ACTIVE and no pending contacts\n"
        "- **pending_activation**: Organizations with status = ACTIVE but have PENDING contacts\n"
        "- **inactive**: Organizations with status = INACTIVE\n"
        "- **suspended**: Organizations with status = SUSPENDED\n\n"
        "Admin only."
    ),
)

# ── Organization CRUD ──────────────────────────────────────────────────────────

CREATE_ORG = create_doc_entry(
    "Create an organisation with contacts",
    {
        201: success_entry(
            "Organisation created",
            data={
                "organization": _ORG_DATA,
                "contacts": [
                    {
                        "contact_id": "00000000-0000-0000-0000-000000000001",
                        "user_id": "00000000-0000-0000-0000-000000000002",
                        "email": "jane.smith@acme-logistics.co.uk",
                        "contact_role": "ACCOUNT_OWNER",
                        "invite_token": "abc123...",
                    }
                ],
                "payment_config": _PAYMENT_CONFIG_DATA,
                "credit_config": None,
                "suspension_config": None,
                "discount_config": _DISCOUNT_CONFIG_DATA,
                "message": "Organisation created. 1 invite(s) queued.",
            },
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        409: error_entry(
            "Duplicate organisation",
            code="CONFLICT",
            message="An organisation with this companies_house_number already exists",
        ),
    },
    description="Creates an organisation, one or more contacts (each receives an invite email), "
    "and optionally a payment configuration — all in a single atomic transaction. "
    "At least one contact with contact_role=ACCOUNT_OWNER is required. Admin only.",
)

LIST_ORGS = create_doc_entry(
    "List organisations",
    {
        200: success_entry(
            "Paginated organisation list",
            data={
                "items": [_ORG_DATA],
                "total": 1,
                "page": 1,
                "page_size": 20,
                "pages": 1,
            },
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
    },
    description="ADMIN or SUPER_ADMIN: paginated list of all orgs with optional search (name, reference, legal name) and status filter. "
    "CUSTOMER_B2B: returns only their own organisation (single item).",
)

GET_ORG = create_doc_entry(
    "Get organisation by ID",
    {
        200: success_entry("Organisation details", data=_ORG_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry("Access denied", code="FORBIDDEN", message="You do not have access to this organisation"),
        404: error_entry("Organisation not found", code="NOT_FOUND", message="Organisation not found"),
    },
    description=_PLATFORM_ADMIN_ORG_ACCESS,
)

UPDATE_ORG = create_doc_entry(
    "Update organisation details (admin)",
    {
        200: success_entry("Organisation updated", data=_ORG_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        404: error_entry("Organisation not found", code="NOT_FOUND", message="Organisation not found"),
    },
    description="Partial update — only supplied fields are changed. reason is mandatory for audit trail. Admin only.",
)

UPDATE_ORG_SELF = create_doc_entry(
    "Save own organisation profile (B2B self-serve)",
    {
        200: success_entry(
            "Organisation profile updated",
            data={
                "organization": _ORG_PROFILE_RESPONSE_ORG,
                "pickup_addresses": [_PICKUP_ADDRESS_PROFILE_ROW],
            },
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="Insufficient permission on ORG_PROFILE: requires WRITE, you have NONE",
        ),
        404: error_entry("Organisation not found", code="NOT_FOUND", message="Organisation not found"),
        422: error_entry(
            "Invalid multipart payload",
            code="VALIDATION_ERROR",
            message="`payload` must be valid JSON matching the profile save schema. Logo must be JPEG/PNG, max 2 MB.",
        ),
    },
    description="Multipart save endpoint for profile changes. Send `payload` as a JSON string with organization "
    "profile fields (including registration fields like `eori_number`, `registered_address`, `trading_address` or "
    "`trading_same_as_registered_address`, and `pickup_addresses`) and "
    "optional `logo` as JPEG/PNG (max 2 MB). "
    "**Response `organization`:** flat registered columns `reg_address_line_1` … `reg_country` and trading columns "
    "`trading_address_line_1` … `trading_address_country` (aligned with the `OrganizationResponse` model in OpenAPI). "
    "When trading is not stored separately, trading lines may match registered or be null depending on data. "
    "Also includes `contract_title`, `contract_url`, `contract_expiry_date`, onboarding (`onboarded_by` / "
    "`onboarded_by_role`), account manager display fields, and `logo_url`. "
    "Onboarding/account-manager fields are response-only (admin-managed): they are not required for profile save "
    "and are not writable through this endpoint. `contract_title`/`contract_url`/`contract_expiry_date` are also "
    "response fields for this profile flow (not updated by PATCH `/profile`). "
    "Use `trading_same_as_registered_address: true` to copy trading address from `registered_address` in the same "
    "request, or from existing org registered columns if registered is omitted; do not send `trading_address` when "
    "that flag is true. "
    "Pickup entries may set `same_as_registered_address` / `same_as_trading_address`; the API persists resolved "
    "line_1/city/postcode etc. (flags are not stored). Validation rules: cannot set both same-as flags to true; "
    "when neither same-as flag is true, manual pickup address fields are required (`line_1`, `city`, `state`, "
    "`postcode`, `country`). If a same-as flag is true and the source org address is incomplete, the request is "
    "rejected with 422 (no fallback copy). "
    "PATCH `/profile` responses use `ProfileSaveSuccessResponse` so optional fields appear as explicit JSON nulls "
    "(stable keys for FE). "
    "ADMIN, SUPER_ADMIN, or CUSTOMER_B2B org contact: ACCOUNT_OWNER or delegate with ORG_PROFILE WRITE may update profile fields. "
    "ADMIN and SUPER_ADMIN bypass ORG_PROFILE checks.\n\n"
    f"**`payload` JSON field reference** (from `OrgProfileSavePayload`; all keys optional unless your validators require them):\n"
    f"{_ORG_PROFILE_SAVE_PAYLOAD_SCHEMA_DOC}",
)

_PROFILE_COMPLETION_DATA = {
    "percent_complete": 35,
    "completed_weight": 35,
    "total_weight": 100,
    "items": [
        {
            "key": "setup_account",
            "label": "Setup Account",
            "weight": 10,
            "completed": True,
            "missing_fields": [],
            "hint": "Activate at least one organization contact account.",
        },
        {
            "key": "company_logo",
            "label": "Upload Company Logo",
            "weight": 5,
            "completed": False,
            "missing_fields": ["company_logo"],
            "hint": "Upload a JPEG/PNG logo.",
        },
        {
            "key": "company_information",
            "label": "Company Information (Name, Address, VAT, EORI)",
            "weight": 15,
            "completed": False,
            "missing_fields": ["eori_number"],
            "hint": "Complete legal details, EORI, VAT, and registered address.",
        },
        {
            "key": "primary_contact_info",
            "label": "Primary Contact Info",
            "weight": 10,
            "completed": False,
            "missing_fields": ["primary_contact.phone"],
            "hint": "Primary contact must have full name and phone number.",
        },
        {
            "key": "security_setup",
            "label": "Security Setup (Password)",
            "weight": 20,
            "completed": True,
            "missing_fields": [],
            "hint": "Complete initial password setup.",
        },
        {
            "key": "receiver_notifications",
            "label": "Receiver Notification Preference",
            "weight": 10,
            "completed": False,
            "missing_fields": ["recipient_notification_preferences"],
            "hint": "Save at least one receiver notification preference.",
        },
        {
            "key": "billing_details",
            "label": "Billing Details / Bank Info",
            "weight": 20,
            "completed": False,
            "missing_fields": ["payment_configuration"],
            "hint": "Configure payment details for the organization.",
        },
        {
            "key": "pickup_addresses",
            "label": "Pickup Addresses",
            "weight": 10,
            "completed": False,
            "missing_fields": ["default_pickup_address"],
            "hint": "Add at least one default pickup address.",
        },
    ],
}

_ORG_PROFILE_GATE_DESCRIPTION = (
    "ADMIN or SUPER_ADMIN: always allowed (no org_contacts row required). "
    "CUSTOMER_B2B: org member; ACCOUNT_OWNER always allowed; "
    "other contacts need ORG_PROFILE permission (READ for this operation, WRITE where noted)."
)

_ORG_PROFILE_GET_DATA = {
    "organization": _ORG_PROFILE_RESPONSE_ORG,
    "pickup_addresses": [_PICKUP_ADDRESS_PROFILE_ROW],
}

GET_ORG_PROFILE = create_doc_entry(
    "Get B2B organisation profile (full screen)",
    {
        200: success_entry("Organisation profile", data=_ORG_PROFILE_GET_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="Insufficient permission on ORG_PROFILE: requires READ, you have NONE",
        ),
        404: error_entry("Organisation not found", code="NOT_FOUND", message="Organisation not found"),
    },
    description=(
        "Same shape as `PATCH /organizations/{org_id}/profile` success: `organization` plus `pickup_addresses`. "
        "`organization` uses flat registered columns (`reg_address_line_1` … `reg_country`) and trading columns "
        "(`trading_address_line_1` … `trading_address_country`), plus EORI/VAT, contract fields, "
        "`logo_url`, onboarding, and account-manager fields — see `OrganizationResponse` in OpenAPI. "
        "Onboarding/account-manager fields are read-only in this profile flow. "
        "Optional fields are returned as explicit JSON nulls (`ProfileSaveSuccessResponse`). "
        "Requires ORG_PROFILE READ (or admin / account owner). "
        f"{_ORG_PROFILE_GATE_DESCRIPTION}"
    ),
)

GET_PROFILE_COMPLETION = create_doc_entry(
    "B2B profile completion checklist",
    {
        200: success_entry("Profile completion", data=_PROFILE_COMPLETION_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="Insufficient permission on ORG_PROFILE: requires READ, you have NONE",
        ),
        404: error_entry("Organisation not found", code="NOT_FOUND", message="Organisation not found"),
    },
    description=(
        "Weighted onboarding checklist (items sum to 100) for the B2B portal. "
        "The `receiver_notifications` item is complete only after the org has at least one row in "
        "`org_notification_preferences` for RECIPIENT (saved via "
        "`PATCH /notifications/preferences/organization/{org_id}/RECIPIENT`). "
        f"{_ORG_PROFILE_GATE_DESCRIPTION}"
    ),
)

UPDATE_ORG_LOGO = create_doc_entry(
    "Upload or replace organisation logo",
    {
        200: success_entry("Logo updated", data={**_ORG_DATA, "logo_url": "https://imagedelivery.net/example/cf-id/public"}),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="Insufficient permission on ORG_PROFILE: requires WRITE, you have NONE",
        ),
        404: error_entry("Organisation not found", code="NOT_FOUND", message="Organisation not found"),
        422: error_entry("Invalid image", code="VALIDATION_ERROR", message="Logo must be JPEG or PNG, max 2 MB"),
    },
    description=f"Multipart file field `logo` — JPEG/PNG, max 2 MB. Stored on Cloudflare Images; response includes `logo_url`. {_ORG_PROFILE_GATE_DESCRIPTION.replace('READ for this operation, WRITE where noted', 'WRITE')}",
)

_PICKUP_ADDRESS_DATA = {
    "id": "00000000-0000-4000-8000-000000000010",
    "organization_id": _ORG_DATA["id"],
    "user_id": None,
    "label": "Main warehouse",
    "line_1": "45 Street Road",
    "line_2": "Unit 3",
    "city": "London",
    "state": "Greater London",
    "postcode": "W8 5ED",
    "country": "United Kingdom",
    "latitude": 51.5007,
    "longitude": -0.1246,
    "is_default": True,
    "created_by_user_id": None,
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z",
    "version": 1,
}

LIST_PICKUP_ADDRESSES = create_doc_entry(
    "List organisation pickup addresses",
    {
        200: success_entry("Pickup addresses", data=[_PICKUP_ADDRESS_DATA]),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="Insufficient permission on ORG_PROFILE: requires READ, you have NONE",
        ),
        404: error_entry("Organisation not found", code="NOT_FOUND", message="Organisation not found"),
    },
    description=f"Returns pickup locations; default address first. Optional `label` identifies a site/warehouse. {_ORG_PROFILE_GATE_DESCRIPTION}",
)

CREATE_PICKUP_ADDRESS = create_doc_entry(
    "Create one or more pickup addresses",
    {
        201: success_entry("Pickup addresses created", data=[_PICKUP_ADDRESS_DATA]),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="Insufficient permission on ORG_PROFILE: requires WRITE, you have NONE",
        ),
        404: error_entry("Organisation not found", code="NOT_FOUND", message="Organisation not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Invalid request body"),
    },
    description=(
        "JSON array — same item shape as `POST /v1/pickup-addresses` (manual lines or "
        "`same_as_registered_address` / `same_as_trading_address`, plus label and coordinates as needed). "
        "Setting `is_default=true` on an item clears default on other addresses. "
        f"{_ORG_PROFILE_GATE_DESCRIPTION.replace('READ for this operation, WRITE where noted', 'WRITE')}"
    ),
)

UPDATE_PICKUP_ADDRESS = create_doc_entry(
    "Update a pickup address",
    {
        200: success_entry("Pickup address updated", data=_PICKUP_ADDRESS_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="Insufficient permission on ORG_PROFILE: requires WRITE, you have NONE",
        ),
        404: error_entry("Pickup address not found", code="NOT_FOUND", message="Pickup address not found for this organisation"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Invalid request body"),
    },
    description=f"Partial update; setting `is_default=true` clears other defaults. {_ORG_PROFILE_GATE_DESCRIPTION.replace('READ for this operation, WRITE where noted', 'WRITE')}",
)

DELETE_PICKUP_ADDRESS = create_doc_entry(
    "Delete a pickup address",
    {
        200: success_entry("Pickup address deleted", message="Pickup address deleted."),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="Insufficient permission on ORG_PROFILE: requires WRITE, you have NONE",
        ),
        404: error_entry("Pickup address not found", code="NOT_FOUND", message="Pickup address not found for this organisation"),
    },
    description=(
        "Removes the address; if it was default, the oldest remaining address becomes default. "
        f"{_ORG_PROFILE_GATE_DESCRIPTION.replace('READ for this operation, WRITE where noted', 'WRITE')}"
    ),
)

CHANGE_ORG_STATUS = create_doc_entry(
    "Change organisation status",
    {
        200: success_entry("Status updated", data={**_ORG_DATA, "status": "SUSPENDED"}),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        404: error_entry("Organisation not found", code="NOT_FOUND", message="Organisation not found"),
        422: error_entry(
            "Invalid status transition",
            code="INVALID_STATE_TRANSITION",
            message="Cannot transition from INACTIVE to SUSPENDED",
        ),
    },
    description="Valid transitions: ACTIVE↔INACTIVE, ACTIVE↔SUSPENDED, SUSPENDED→ACTIVE. " "INACTIVE→SUSPENDED is not allowed. reason is mandatory. Admin only.",
)

DELETE_ORG = create_doc_entry(
    "Soft-delete an organisation",
    {
        200: success_entry("Organisation deactivated", message="Organisation deactivated."),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        404: error_entry("Organisation not found", code="NOT_FOUND", message="Organisation not found"),
    },
    description="Sets the organisation status to INACTIVE. Admin only.",
)

# ── Org Contacts ──────────────────────────────────────────────────────────────

LIST_CONTACTS = create_doc_entry(
    "List contacts for an organisation",
    {
        200: success_entry("Contact list", data=[_CONTACT_DATA]),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry("Access denied", code="FORBIDDEN", message="You do not have access to this organisation"),
    },
    description="Admin or same-org CUSTOMER_B2B: full contact details (name, email, phone). " "Other authenticated callers: name and role only (GDPR scoping).",
)

GET_CONTACT = create_doc_entry(
    "Get a single contact",
    {
        200: success_entry("Contact details", data=_CONTACT_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry("Access denied", code="FORBIDDEN", message="You do not have access to this organisation"),
        404: error_entry("Contact not found", code="NOT_FOUND", message="Contact not found"),
    },
)

ADD_CONTACT = create_doc_entry(
    "Add a contact to an organisation",
    {
        201: success_entry("Contact added", data=_CONTACT_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry("Access denied", code="FORBIDDEN", message="Only ACCOUNT_OWNER contacts may add contacts"),
        409: error_entry(
            "User already a contact",
            code="CONFLICT",
            message="This user is already an active contact of the organisation",
        ),
    },
    description="Creates a CUSTOMER_B2B user (if new), an OrgContact row, applies any permission overrides, "
    "and sends an invite email. Allowed callers: ADMIN or same-org ACCOUNT_OWNER.",
)

UPDATE_CONTACT = create_doc_entry(
    "Update a contact",
    {
        200: success_entry("Contact updated", data=_CONTACT_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry("Access denied", code="FORBIDDEN", message="Only ACCOUNT_OWNER contacts may update contacts"),
        404: error_entry("Contact not found", code="NOT_FOUND", message="Contact not found"),
    },
    description="Updates contact_number, contact_role, and/or permission overrides. "
    "Providing permissions replaces all existing overrides for the contact's user. "
    "Allowed callers: ADMIN or same-org ACCOUNT_OWNER.",
)

REMOVE_CONTACT = create_doc_entry(
    "Remove a contact from an organisation",
    {
        200: success_entry("Contact removed", message="Contact removed."),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry("Access denied", code="FORBIDDEN", message="Only ACCOUNT_OWNER contacts may remove contacts"),
        404: error_entry("Contact not found", code="NOT_FOUND", message="Contact not found"),
        409: error_entry(
            "Cannot remove last contact",
            code="CONFLICT",
            message="Cannot remove the last active contact of an organisation",
        ),
    },
    description="Soft-deletes the contact (sets status=INACTIVE). " "Will fail if this is the last active contact. Allowed callers: ADMIN or same-org ACCOUNT_OWNER.",
)

ISSUE_CONTACT_SUPPORT_PASSWORD = create_doc_entry(
    "Set support-issued password for an organisation contact",
    {
        200: success_entry(
            "Password reset",
            data={"user_id": "00000000-0000-0000-0000-000000000000", "email": "user@example.com"},
            message="Password reset. The user was signed out of all sessions.",
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Forbidden",
            code="FORBIDDEN",
            message="Requires RESET_B2B_CLIENT_PASSWORDS at WRITE level",
        ),
        404: error_entry("Contact not found", code="NOT_FOUND", message="Contact not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Weak new_password or same as current password",
        ),
    },
    description=(
        "Support flow: request body supplies ``new_password`` (validated strength). Sets the contact user's password, "
        "sets ``force_password_change``, invalidates existing sessions, and emails the plaintext password. "
        "Requires **WRITE** on **RESET_B2B_CLIENT_PASSWORDS** (enforced on the route)."
    ),
)

SET_PRIMARY_CONTACT = create_doc_entry(
    "Set primary contact for an organisation",
    {
        200: success_entry("Primary contact updated", data={**_CONTACT_DATA, "is_primary": True}),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry("Access denied", code="FORBIDDEN", message="Only ACCOUNT_OWNER contacts may change the primary contact"),
        404: error_entry("Contact not found", code="NOT_FOUND", message="Contact not found"),
    },
    description="Clears is_primary on all other contacts atomically, then sets is_primary=True on the specified contact. " "Allowed callers: ADMIN or same-org ACCOUNT_OWNER.",
)

# ── Payment Configuration ──────────────────────────────────────────────────────

GET_PAYMENT_CONFIG = create_doc_entry(
    "Get payment configuration",
    {
        200: success_entry("Payment configuration", data=_PAYMENT_CONFIG_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry("Access denied", code="FORBIDDEN", message="You do not have access to this organisation"),
        404: error_entry("Organisation not found", code="NOT_FOUND", message="Organisation not found"),
    },
    description=(
        f"{_PLATFORM_ADMIN_ORG_ACCESS} "
        "If no org-specific config row exists yet, the API creates and returns "
        "fallback defaults from the global delivery-attempt settings."
    ),
)

UPDATE_PAYMENT_CONFIG = create_doc_entry(
    "Update payment configuration",
    {
        200: success_entry("Payment configuration updated", data=_PAYMENT_CONFIG_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        404: error_entry("Organisation not found", code="NOT_FOUND", message="Organisation not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="CARD payment model only supports IMMEDIATE billing schedule.",
        ),
    },
    description=(
        "Partial update — only supplied fields are changed and cross-field rules are "
        "validated against the merged state. reason is mandatory. Admin only. "
        "If delivery_attempt_fees/return_attempt_fees are provided, max_* values are "
        "derived from array lengths when omitted (or must match when provided). "
        "Payment model rules: CARD→IMMEDIATE only; BANK_TRANSFER/CREDIT_ACCOUNT→"
        "FIXED_MONTHLY_DATE or DAYS_AFTER_ORDER."
    ),
)

DELETE_PAYMENT_CONFIG = create_doc_entry(
    "Delete payment configuration",
    {
        200: success_entry("Payment configuration deleted", message="Payment configuration deleted."),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        404: error_entry("Payment config not found", code="NOT_FOUND", message="No payment configuration found for this organisation"),
    },
    description=(
        "Hard-deletes the payment configuration row. Admin only. "
        "A later GET on the same org recreates fallback defaults from global settings."
    ),
)

# ── Document Access OTP ────────────────────────────────────────────────────────

SEND_DOC_OTP = create_doc_entry(
    "Request a document access OTP",
    {
        200: success_entry(
            "OTP sent",
            data={"message": "OTP sent to your registered email address. It expires in 10 minutes."},
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        422: error_entry(
            "Rate limit exceeded",
            code="VALIDATION_ERROR",
            message="Too many OTP requests. Please wait before requesting another.",
        ),
    },
    description=(
        "Generates a 6-digit one-time password and sends it to the authenticated user's registered email. "
        "**Rate limit:** max 3 requests per 10-minute window. "
        "Does **not** require `X-Doc-Access-Token`.\n\n"
        "```\n"
        "curl -X POST http://localhost:8000/v1/organizations/documents/otp/send \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Client-Type: ADMIN"\n'
        "```\n\n"
        "**vs driver compliance documents:** Driver admins use a separate flow: "
        "`POST /v1/drivers/documents/otp/send` → `.../verify` and header **`X-Driver-Doc-Access-Token`** only on "
        "`/v1/drivers/.../documents` routes. Org and driver OTPs/tokens are not interchangeable."
    ),
)

VERIFY_DOC_OTP = create_doc_entry(
    "Verify OTP and receive a document access token",
    {
        200: success_entry(
            "OTP verified — doc access token issued",
            data={
                "doc_access_token": "a3f1c2e4b5d6..." * 4,
                "expires_in": 3600,
                "expires_at": "2026-04-01T13:00:00Z",
                "message": "Document access granted for 1 hour.",
            },
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired OTP"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="OTP must be exactly 6 digits"),
        429: error_entry(
            "OTP verify rate limit or lockout",
            code="RATE_LIMIT_EXCEEDED",
            message="Too many verify attempts. Please try again later or request a new OTP.",
        ),
    },
    description=(
        "Submit the 6-digit OTP received by email. On success, returns a `doc_access_token` "
        "(64-char hex, valid for 1 hour) that must be included as `X-Doc-Access-Token` on all "
        "document management endpoints. OTPs are single-use and expire after 10 minutes. "
        "Verify is rate-limited per IP; repeated invalid attempts per user may trigger a temporary lockout. "
        "Does **not** require `X-Doc-Access-Token`.\n\n"
        "```\n"
        "curl -X POST http://localhost:8000/v1/organizations/documents/otp/verify \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Client-Type: ADMIN" \\\n'
        '  -H "Content-Type: application/json" \\\n'
        "  -d '{\"otp\": \"123456\"}'\n"
        "```\n\n"
        "**vs driver compliance documents:** See note on send-OTP — use **`X-Doc-Access-Token`** here only for "
        "organisation document APIs under `/v1/organizations/...`."
    ),
)

# ── Contract Documents ─────────────────────────────────────────────────────────

_DOC_DATA = {
    "id": "00000000-0000-0000-0000-000000000010",
    "organization_id": "00000000-0000-0000-0000-000000000000",
    "reference": "DOC-2026-00001",
    "title": "Master Service Agreement",
    "document_type": "MSA",
    "category": "CONTRACTS",
    "file_name": "msa-acme-2026.pdf",
    "file_size_bytes": 204800,
    "mime_type": "application/pdf",
    "storage_key": "orgs/00000000/docs/msa-acme-2026.pdf",
    "confidentiality_level": "CONFIDENTIAL",
    "issuing_authority": "Swift Couriers Ltd",
    "issue_date": "2026-01-01",
    "expiry_date": "2027-12-31",
    "description": "Annual master service agreement for 2026.",
    "tags": ["MSA", "2026"],
    "notify_client": False,
    "is_active": True,
    "uploaded_by_id": "00000000-0000-0000-0000-000000000002",
    "download_url": "https://r2.example.com/presigned/...",
    "created_at": "2026-01-01T09:00:00Z",
    "updated_at": "2026-01-01T09:00:00Z",
    "version": 1,
}

_DOC_ACTIVITY_DATA = {
    "id": "00000000-0000-0000-0000-000000000020",
    "organization_id": "00000000-0000-0000-0000-000000000000",
    "document_id": "00000000-0000-0000-0000-000000000010",
    "document_name": "Master Service Agreement",
    "action": "UPLOADED",
    "actor_email": "admin@swiftcouriers.co.uk",
    "actor_role": "ADMIN",
    "details": "Uploaded via admin portal",
    "created_at": "2026-01-01T09:00:00Z",
}

UPLOAD_ORG_DOCUMENT = create_doc_entry(
    "Upload a contract document (admin form)",
    {
        201: success_entry("Document uploaded", data=_DOC_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token or missing X-Doc-Access-Token"),
        404: error_entry("Organisation not found", code="NOT_FOUND", message="Organisation not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="File too large or unsupported format"),
    },
    description=(
        "Upload a contract or agreement document (`multipart/form-data`). "
        "Accepted formats: `.pdf`, `.png`, `.jpeg`, `.docx`, `.heic` — max **25 MB**. "
        "**Requires `X-Doc-Access-Token`** (obtain via OTP flow). Admin only.\n\n"
        "**Document types:** `MSA` · `SLA` · `PRICING` · `NDA` · `DPA`\n\n"
        "```\n"
        "curl -X POST http://localhost:8000/v1/organizations/<org_id>/documents \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Doc-Access-Token: <doc_access_token>" \\\n'
        '  -F "document_file=@/path/to/contract.pdf;type=application/pdf" \\\n'
        '  -F "title=Master Service Agreement" \\\n'
        '  -F "document_type=MSA" \\\n'
        '  -F "expiry_date=2028-12-31"\n'
        "```"
    ),
)

UPLOAD_ORG_DOCUMENT_OPERATIONS = create_doc_entry(
    "Upload a document (full operations form)",
    {
        201: success_entry("Document uploaded", data=_DOC_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token or missing X-Doc-Access-Token"),
        404: error_entry("Organisation not found", code="NOT_FOUND", message="Organisation not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="tags must be a JSON array with at most 10 items."),
    },
    description=(
        "Upload a document with full classification fields: category, issuing authority, "
        "issue/expiry dates, description, confidentiality level, and up to 10 custom tags. "
        "Accepted formats: `.pdf`, `.png`, `.jpeg`, `.docx` — max **25 MB**. "
        "**Requires `X-Doc-Access-Token`**. Admin only.\n\n"
        "```\n"
        "curl -X POST http://localhost:8000/v1/organizations/<org_id>/documents/operations \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Doc-Access-Token: <doc_access_token>" \\\n'
        '  -F "document_file=@/path/to/policy.pdf;type=application/pdf" \\\n'
        '  -F "title=Pricing Schedule" \\\n'
        '  -F "document_type=PRICING" \\\n'
        '  -F "category=CONTRACTS" \\\n'
        '  -F "confidentiality_level=INTERNAL" \\\n'
        "  -F 'tags=[\"pricing\",\"2026\"]' \\\n"
        '  -F "expiry_date=2026-08-12"\n'
        "```"
    ),
)

LIST_ORG_DOCUMENTS = create_doc_entry(
    "List documents for an organisation",
    {
        200: success_entry("Document list", data=[_DOC_DATA]),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token or missing X-Doc-Access-Token"),
        403: error_entry("Access denied", code="FORBIDDEN", message="You do not have access to this organisation"),
    },
    description=(
        "Returns all active documents for the organisation, newest first. "
        "**Requires `X-Doc-Access-Token`**. ADMIN or SUPER_ADMIN: any org. CUSTOMER_B2B: own org only.\n\n"
        "```\n"
        "curl http://localhost:8000/v1/organizations/<org_id>/documents \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Doc-Access-Token: <doc_access_token>"\n'
        "```"
    ),
)

GET_ORG_DOCUMENT = create_doc_entry(
    "Get a single document",
    {
        200: success_entry("Document details", data=_DOC_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token or missing X-Doc-Access-Token"),
        403: error_entry("Access denied", code="FORBIDDEN", message="You do not have access to this organisation"),
        404: error_entry("Document not found", code="NOT_FOUND", message="document not found"),
    },
    description=(
        "Returns document metadata and a fresh presigned download URL (valid 1 hour). "
        "**Requires `X-Doc-Access-Token`**. ADMIN or SUPER_ADMIN: any org. CUSTOMER_B2B: own org only.\n\n"
        "```\n"
        "curl http://localhost:8000/v1/organizations/<org_id>/documents/<doc_id> \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Doc-Access-Token: <doc_access_token>"\n'
        "```"
    ),
)

UPDATE_ORG_DOCUMENT = create_doc_entry(
    "Update document metadata",
    {
        200: success_entry("Document updated", data=_DOC_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token or missing X-Doc-Access-Token"),
        404: error_entry("Document not found", code="NOT_FOUND", message="document not found"),
    },
    description=(
        "Partial update — only supplied fields are changed. `reason` is mandatory for the audit trail. "
        "**Requires `X-Doc-Access-Token`**. Admin only.\n\n"
        "```\n"
        "curl -X PATCH http://localhost:8000/v1/organizations/<org_id>/documents/<doc_id> \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Doc-Access-Token: <doc_access_token>" \\\n'
        '  -H "Content-Type: application/json" \\\n'
        "  -d '{\"title\": \"Updated Agreement\", \"document_type\": \"DPA\", \"reason\": \"Contract reclassified\"}'\n"
        "```"
    ),
)

DELETE_ORG_DOCUMENT = create_doc_entry(
    "Soft-delete a document",
    {
        200: success_entry("Document deleted", message="Document deleted."),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token or missing X-Doc-Access-Token"),
        404: error_entry("Document not found", code="NOT_FOUND", message="document not found"),
    },
    description=(
        "Sets `is_active=False` on the document (soft delete). "
        "**Requires `X-Doc-Access-Token`**. Admin only.\n\n"
        "```\n"
        "curl -X DELETE http://localhost:8000/v1/organizations/<org_id>/documents/<doc_id> \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Doc-Access-Token: <doc_access_token>"\n'
        "```"
    ),
)

LIST_ORG_DOCUMENT_ACTIVITIES = create_doc_entry(
    "List document activity log",
    {
        200: success_entry("Activity log", data=[_DOC_ACTIVITY_DATA]),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token or missing X-Doc-Access-Token"),
        403: error_entry("Access denied", code="FORBIDDEN", message="You do not have access to this organisation"),
    },
    description=(
        "Returns the recent-activity audit log for all documents in the organisation, newest first. "
        "Each row records one action (UPLOADED, DOWNLOADED, EXPIRED, DELETED) with actor details. "
        "**Requires `X-Doc-Access-Token`**. ADMIN or SUPER_ADMIN: any org. CUSTOMER_B2B: own org only.\n\n"
        "| Query param | Default | Max |\n"
        "|---|---|---|\n"
        "| `limit` | 100 | 500 |\n\n"
        "```\n"
        "curl 'http://localhost:8000/v1/organizations/<org_id>/documents/activities?limit=50' \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Doc-Access-Token: <doc_access_token>"\n'
        "```"
    ),
)

# ── Document Sharing ───────────────────────────────────────────────────────────

_SHARE_DATA = {
    "id": "00000000-0000-0000-0000-000000000030",
    "organization_id": "00000000-0000-0000-0000-000000000000",
    "document_id": "00000000-0000-0000-0000-000000000010",
    "share_token": "abc123xyz...",
    "recipient_email": "client@example.com",
    "recipient_name": "John Doe",
    "message": "Please review and sign the attached MSA.",
    "status": "ACTIVE",
    "access_count": 0,
    "expires_at": "2026-04-08T09:00:00Z",
    "shared_by_id": "00000000-0000-0000-0000-000000000002",
    "created_at": "2026-04-01T09:00:00Z",
    "updated_at": "2026-04-01T09:00:00Z",
    "version": 1,
}

LIST_ORG_DOCUMENT_SHARES = create_doc_entry(
    "List all document shares for an organisation",
    {
        200: success_entry(
            "Paginated share list",
            data={"items": [_SHARE_DATA], "total": 1, "page": 1, "page_size": 50, "pages": 1},
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token or missing X-Doc-Access-Token"),
        403: error_entry("Access denied", code="FORBIDDEN", message="You do not have access to this organisation"),
    },
    description=(
        "Paginated list of all sharing links for an organisation. "
        "Optional `status` filter: `ACTIVE` · `DOWNLOADED` · `EXPIRED` · `REVOKED`. "
        "**Requires `X-Doc-Access-Token`**. ADMIN or SUPER_ADMIN: any org. CUSTOMER_B2B: own org only.\n\n"
        "| Query param | Default | Description |\n"
        "|---|---|---|\n"
        "| `page` | 1 | Page number |\n"
        "| `size` | 50 | Items per page |\n"
        "| `status` | — | Filter by share status |\n\n"
        "```\n"
        "curl 'http://localhost:8000/v1/organizations/<org_id>/documents/shares?status=ACTIVE' \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Doc-Access-Token: <doc_access_token>"\n'
        "```"
    ),
)

SHARE_DOCUMENT = create_doc_entry(
    "Create a document sharing link",
    {
        201: success_entry("Share created", data=_SHARE_DATA),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token or missing X-Doc-Access-Token"),
        404: error_entry("Document not found", code="NOT_FOUND", message="document not found"),
    },
    description=(
        "Generates a time-limited sharing link for a document and optionally sends an email to the recipient. "
        "**Requires `X-Doc-Access-Token`**. Admin only.\n\n"
        "```\n"
        "curl -X POST http://localhost:8000/v1/organizations/<org_id>/documents/<doc_id>/shares \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Doc-Access-Token: <doc_access_token>" \\\n'
        '  -H "Content-Type: application/json" \\\n'
        "  -d '{\n"
        '    "recipient_email": "client@example.com",\n'
        '    "recipient_name": "John Doe",\n'
        '    "message": "Please review the attached MSA.",\n'
        '    "expires_at": "2026-04-08T09:00:00Z"\n'
        "  }'\n"
        "```"
    ),
)

LIST_DOCUMENT_SHARES = create_doc_entry(
    "List sharing links for a specific document",
    {
        200: success_entry("Share list", data=[_SHARE_DATA]),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token or missing X-Doc-Access-Token"),
        404: error_entry("Document not found", code="NOT_FOUND", message="document not found"),
    },
    description=(
        "Returns all sharing links for a single document, newest first. "
        "**Requires `X-Doc-Access-Token`**. Admin only.\n\n"
        "```\n"
        "curl http://localhost:8000/v1/organizations/<org_id>/documents/<doc_id>/shares \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Doc-Access-Token: <doc_access_token>"\n'
        "```"
    ),
)

EXTEND_SHARE_EXPIRY = create_doc_entry(
    "Extend a document share expiry",
    {
        200: success_entry("Expiry extended", data={**_SHARE_DATA, "expires_at": "2026-04-15T09:00:00Z"}),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token or missing X-Doc-Access-Token"),
        404: error_entry("Share not found", code="NOT_FOUND", message="OrgDocumentShare not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="New expiry must be in the future",
        ),
    },
    description=(
        "Updates the `expires_at` of an existing share link. "
        "The new expiry must be in the future. "
        "**Requires `X-Doc-Access-Token`**. Admin only.\n\n"
        "```\n"
        "curl -X PATCH http://localhost:8000/v1/organizations/<org_id>/documents/shares/<share_id>/expiry \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Doc-Access-Token: <doc_access_token>" \\\n'
        '  -H "Content-Type: application/json" \\\n'
        '  -d \'{"expires_at": "2026-04-15T09:00:00Z"}\'\n'
        "```"
    ),
)

REVOKE_DOCUMENT_SHARE = create_doc_entry(
    "Revoke a document share",
    {
        200: success_entry("Share revoked", data={**_SHARE_DATA, "status": "REVOKED"}),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token or missing X-Doc-Access-Token"),
        404: error_entry("Share not found", code="NOT_FOUND", message="OrgDocumentShare not found"),
        422: error_entry(
            "Already revoked",
            code="VALIDATION_ERROR",
            message="Share is already revoked",
        ),
    },
    description=(
        "Sets the share status to `REVOKED`, immediately invalidating the public link. "
        "**Requires `X-Doc-Access-Token`**. Admin only.\n\n"
        "```\n"
        "curl -X PATCH http://localhost:8000/v1/organizations/<org_id>/documents/shares/<share_id>/revoke \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Doc-Access-Token: <doc_access_token>"\n'
        "```"
    ),
)

_ORG_CARDS_READ_DOC = f"Braintree saved cards (vault) for the organisation, via the shared payments service. {_ORG_PROFILE_GATE_DESCRIPTION}"

_ORG_CARDS_WRITE_DOC = f"Braintree saved cards (vault) for the organisation, via the shared payments service. {_ORG_PROFILE_GATE_DESCRIPTION.replace('READ for this operation, WRITE where noted', 'WRITE')}"

_ORG_CARDS_B2B_SELF_SERVE_NOTE = (
    "CUSTOMER_B2B org members only — administrators cannot use this operation "
    "(list, get one card, set default, and delete remain available to admins with org access). "
    "ACCOUNT_OWNER always allowed; other contacts need ORG_PROFILE at the level noted for each route."
)

ORG_CARDS_BRAINTREE_TOKEN = create_doc_entry(
    "Get Braintree client token (organisation cards)",
    {
        200: success_entry("Client token", data={"client_token": "sandbox_client_token_..."}),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="This action is only available to organisation members, or insufficient ORG_PROFILE READ.",
        ),
    },
    description=(
        "``GET /v1/organizations/{org_id}/payment-methods/cards/braintree-client-token``. "
        "Client token for Hosted Fields / Drop-in when adding a card to this org vault. "
        f"{_ORG_CARDS_B2B_SELF_SERVE_NOTE}"
    ),
)

ORG_CARDS_LIST = create_doc_entry(
    "List saved credit cards for an organisation",
    {
        200: success_entry("Saved cards", data=[{"id": "...", "card_type": "VISA", "last_four": "4242"}]),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="Insufficient permission on ORG_PROFILE: requires READ, you have NONE",
        ),
    },
    description=f"``GET /v1/organizations/{{org_id}}/payment-methods/cards``. {_ORG_CARDS_READ_DOC}",
)

ORG_CARDS_CREATE = create_doc_entry(
    "Save a new credit card for an organisation",
    {
        201: success_entry("Card saved", message="Card saved successfully", data={"id": "...", "last_four": "4242"}),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="This action is only available to organisation members, or insufficient ORG_PROFILE WRITE.",
        ),
        409: error_entry(
            "Duplicate card",
            code="CONFLICT",
            message="This card is already saved.",
        ),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Card verification failed"),
    },
    description=(
        "``POST /v1/organizations/{org_id}/payment-methods/cards``. "
        "Nonce from client-side Braintree tokenization with 3D Secure; the API verifies liability shift server-side before vaulting. "
        f"{_ORG_CARDS_B2B_SELF_SERVE_NOTE}"
    ),
)

ORG_CARDS_GET = create_doc_entry(
    "Get one saved credit card for an organisation",
    {
        200: success_entry("Card", data={"id": "...", "last_four": "4242"}),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="Insufficient permission on ORG_PROFILE: requires READ, you have NONE",
        ),
        404: error_entry("Not found", code="NOT_FOUND", message="credit_card with id '...' not found"),
    },
    description=f"``GET /v1/organizations/{{org_id}}/payment-methods/cards/{{card_id}}``. {_ORG_CARDS_READ_DOC}",
)

ORG_CARDS_PREPARE_PAYMENT = create_doc_entry(
    "Prepare checkout nonce for org card (3DS)",
    {
        200: success_entry(
            "Nonce for verifyCard",
            data={"nonce": "tokenization_key_nonce_abc", "bin": "411111"},
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="This action is only available to organisation members, or insufficient ORG_PROFILE READ.",
        ),
        404: error_entry("Not found", code="NOT_FOUND", message="credit_card with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Could not start card verification. Try again.",
        ),
    },
    description=(
        "``POST /v1/organizations/{org_id}/payment-methods/cards/prepare-payment``. "
        "Body: ``card_id``. Returns a one-time nonce for Braintree ``threeDSecure.verifyCard`` with the real order amount. "
        f"{_ORG_CARDS_B2B_SELF_SERVE_NOTE}"
    ),
)

ORG_CARDS_SET_DEFAULT = create_doc_entry(
    "Set default credit card for an organisation",
    {
        200: success_entry("Default updated", message="Default card updated", data={"id": "...", "is_default": True}),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="Insufficient permission on ORG_PROFILE: requires WRITE, you have NONE",
        ),
        404: error_entry("Not found", code="NOT_FOUND", message="credit_card with id '...' not found"),
    },
    description=f"``PATCH /v1/organizations/{{org_id}}/payment-methods/cards/{{card_id}}/default``. {_ORG_CARDS_WRITE_DOC}",
)

ORG_CARDS_MARK_DEFAULT = create_doc_entry(
    "Mark card as default for an organisation",
    {
        200: success_entry("Default updated", message="Card marked as default", data={"id": "...", "is_default": True}),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="Insufficient permission on ORG_PROFILE: requires WRITE, you have NONE",
        ),
        404: error_entry("Not found", code="NOT_FOUND", message="credit_card with id '...' not found"),
    },
    description=f"``PATCH /v1/organizations/{{org_id}}/payment-methods/cards/{{card_id}}/mark-default``. {_ORG_CARDS_WRITE_DOC}",
)

ORG_CARDS_UNMARK_DEFAULT = create_doc_entry(
    "Unmark card as default for an organisation",
    {
        200: success_entry("Default updated", message="Card unmarked as default", data={"id": "...", "is_default": False}),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="Insufficient permission on ORG_PROFILE: requires WRITE, you have NONE",
        ),
        404: error_entry("Not found", code="NOT_FOUND", message="credit_card with id '...' not found"),
    },
    description=f"``PATCH /v1/organizations/{{org_id}}/payment-methods/cards/{{card_id}}/unmark-default``. {_ORG_CARDS_WRITE_DOC}",
)

ORG_CARDS_DELETE = create_doc_entry(
    "Delete a saved credit card for an organisation",
    {
        200: success_entry("Card removed", message="Card removed"),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Access denied",
            code="FORBIDDEN",
            message="Insufficient permission on ORG_PROFILE: requires WRITE, you have NONE",
        ),
        404: error_entry("Not found", code="NOT_FOUND", message="credit_card with id '...' not found"),
    },
    description=f"``DELETE /v1/organizations/{{org_id}}/payment-methods/cards/{{card_id}}``. Removes the row and the Braintree payment method. {_ORG_CARDS_WRITE_DOC}",
)
