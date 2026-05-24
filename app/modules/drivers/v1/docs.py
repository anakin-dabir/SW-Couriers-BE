"""OpenAPI documentation entries for driver endpoints."""

from __future__ import annotations

from typing import Any

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry

LIST_DRIVERS = create_doc_entry(
    "List drivers with pagination, search, filters and KPIs",
    {
        200: success_entry(
            "Paginated list of drivers with KPIs",
            data={
                "kpis": {
                    "total_employed": 120,
                    "active_now": 87,
                    "pending_activation": 5,
                    "suspended": 3,
                },
                "table": {
                    "items": [
                        {
                            "id": "00000000-0000-0000-0000-000000000000",
                            "user_id": "00000000-0000-0000-0000-000000000001",
                            "driver_code": "DR-001",
                            "first_name": "Jane",
                            "last_name": "Driver",
                            "phone": "07123456789",
                            "capacities": ["VAN"],
                            "account_status": "ACTIVE",
                            "live_status": "ON_ROUTE",
                            "safety_score": 98,
                            "created_at": "2024-01-01T00:00:00Z",
                            "updated_at": "2024-01-01T00:00:00Z",
                            "version": 1,
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "size": 20,
                    "pages": 1,
                },
            },
        ),
        401: error_401_entry(),
        403: error_entry(
            "Insufficient permission (DRIVERS read required)",
            code="FORBIDDEN",
            message="Not allowed",
        ),
    },
    description=(
        "Requires Resource.DRIVERS READ. "
        "Supports pagination via page/size and sorting via order_by/order_desc. "
        "Optional query params: "
        "search (matches driver_code like 'DR-001', linked user full name, or linked user phone), "
        "account_status as repeated list keys (e.g. account_status=ACTIVE&account_status=SUSPENDED). "
        "By default, DRAFT drivers are excluded — use /v1/drivers/drafts for drafts, or explicitly filter account_status=DRAFT. "
        "live_status as repeated list keys (e.g. live_status=ON_ROUTE&live_status=OFFLINE). "
        "Additional filter: depot_id. "
        "Response includes KPIs (`total_employed`, `active_now`, `pending_activation`, `suspended`). "
        "`total_employed` is the default-unfiltered driver list total (drafts/unlinked excluded); "
        "it does **not** follow `search`, `depot_id`, or other list filters (`table.total` may differ when those are used). "
        "`active_now` / `pending_activation` / `suspended` count linked-user drivers globally. "
        "Also returns a paginated table of drivers with driver_code, "
        "identity fields from the linked user, capacities, account_status, live_status and safety_score."
    ),
)

LIST_DRIVER_DRAFTS = create_doc_entry(
    "List draft drivers with pagination, search, and filters",
    {
        200: success_entry(
            "Paginated list of draft drivers",
            data={
                "table": {
                    "items": [
                        {
                            "id": "00000000-0000-0000-0000-000000000000",
                            "user_id": "00000000-0000-0000-0000-000000000001",
                            "driver_code": "DR-001",
                            "draft_id": "DF-001",
                            "draft_created_by": "00000000-0000-0000-0000-000000000099",
                            "draft_created_at": "2024-01-01T00:00:00Z",
                            "draft_updated_at": "2024-01-02T00:00:00Z",
                            "email": "driver@example.com",
                            "first_name": "Jane",
                            "last_name": "Driver",
                            "phone": "07123456789",
                            "capacities": ["VAN"],
                            "driver_type": "INTERNAL",
                            "city": "London",
                            "postcode": "SW1A 1AA",
                            "account_status": "DRAFT",
                            "live_status": "OFFLINE",
                            "safety_score": 98,
                            "created_at": "2024-01-01T00:00:00Z",
                            "updated_at": "2024-01-01T00:00:00Z",
                            "version": 1,
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "size": 20,
                    "pages": 1,
                },
            },
        ),
        401: error_401_entry(),
        403: error_entry(
            "Insufficient permission (DRIVERS read required)",
            code="FORBIDDEN",
            message="Not allowed",
        ),
    },
    description=(
        "Requires Resource.DRIVERS READ. "
        "Lists only drivers with `account_status=DRAFT`. "
        "Supports pagination via page/size and sorting via order_by/order_desc. "
        "Optional query params: "
        "search (matches draft_id like 'DF-001', driver_code like 'DR-001', or draft identity fields stored in `driver_drafts.draft_data` such as email, phone, and full name), "
        "Additional filter: depot_id."
    ),
)

GET_DRIVER_DRAFT = create_doc_entry(
    "Get driver draft by driver_id",
    {
        200: success_entry(
            "Driver draft",
            data={
                "draft_id": "DF-001",
                "driver": {"id": "00000000-0000-0000-0000-000000000000", "account_status": "DRAFT"},
            },
        ),
        401: error_401_entry(),
        403: error_entry(
            "Insufficient permission (DRIVERS read required)",
            code="FORBIDDEN",
            message="Not allowed",
        ),
        404: error_entry("Draft not found", code="NOT_FOUND", message="driver_draft with id '...' not found"),
    },
    description=(
        "Requires Resource.DRIVERS READ. Fetches the draft pivot row for a driver id and returns the current draft snapshot. "
        "This is safe to call for both submitted and non-submitted drafts (useful for UI hydration from JSONB `draft_data`). "
        "`driver.documents[].file_url` is always null in this endpoint. "
        "Use OTP-guarded `/v1/drivers/.../documents` APIs with `X-Driver-Doc-Access-Token` for presigned document access. "
        "`driver.profile_photo_url` is a separate signed Cloudflare Images URL when a profile photo exists and signing works; otherwise `null`."
    ),
)

CREATE_DRIVER_DRAFT = create_doc_entry(
    "Create a driver draft (multipart; optional documents)",
    {
        201: success_entry(
            "Driver draft created",
            data={
                "draft_id": "DF-001",
                "driver": {
                    "id": "00000000-0000-0000-0000-000000000000",
                    "user_id": None,
                    "driver_code": "DR-001",
                    "user": {"id": "00000000-0000-0000-0000-000000000000", "email": "driver@example.com", "first_name": "Jane", "last_name": "Driver", "phone": "07123456789"},
                    "capacities": None,
                    "driver_type": None,
                    "address_line1": None,
                    "city": None,
                    "postcode": None,
                    "account_status": "DRAFT",
                    "live_status": "OFFLINE",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "version": 1,
                },
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission (DRIVERS write required)", code="FORBIDDEN", message="Not allowed"),
        409: error_entry("Conflict", code="CONFLICT", message="A conflicting resource already exists."),
    },
    description=(
        "Multipart endpoint that creates a draft profile (`account_status=DRAFT`) with no linked user yet. "
        "Uses the same form-data style as final submit to keep frontend payload format consistent. "
        "Driving licence documents are optional on draft save; when provided, include `documents` + `documents_metadata` "
        "with DRIVING_LICENCE metadata. Draft id is generated in `driver_drafts` (DF-NNN). "
        "Requires Resource.DRIVERS WRITE. Optional licence upload here does **not** use the driver document OTP header "
        "(use OTP for `/v1/drivers/.../documents` CRUD routes only). "
        "Response `driver.documents[].file_url` values are presigned when storage signing is configured."
    ),
)

CREATE_DRIVER_DRAFT["openapi_extra"] = {
    "requestBody": {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                                "email": {"type": "string", "nullable": True},
                                "first_name": {"type": "string", "nullable": True},
                                "last_name": {"type": "string", "nullable": True},
                                "phone": {"type": "string", "nullable": True},
                        "driver_type": {"type": "string", "nullable": True},
                        "address_line1": {"type": "string", "nullable": True},
                        "address_line2": {"type": "string", "nullable": True},
                        "country": {"type": "string", "nullable": True},
                        "state": {"type": "string", "nullable": True},
                        "city": {"type": "string", "nullable": True},
                        "postcode": {"type": "string", "nullable": True},
                        "latitude": {"type": "number", "nullable": True},
                        "longitude": {"type": "number", "nullable": True},
                        "depot_id": {"type": "string", "nullable": True},
                        "vehicle_id": {"type": "string", "nullable": True},
                        "license_number": {"type": "string", "nullable": True},
                        "license_category": {"type": "string", "nullable": True},
                        "max_stops": {"type": "integer", "nullable": True},
                        "okay_with_layover": {"type": "boolean", "nullable": True},
                        "layover_cost_per_night": {
                            "type": "string",
                            "nullable": True,
                            "description": "Optional GBP amount per night (decimal string, e.g. 85.00).",
                        },
                        "max_layover_nights": {"type": "integer", "nullable": True},
                        "capacity[0]": {"type": "string", "description": "Optional first capacity value (VAN/TRUCK)."},
                        "documents_metadata": {"type": "string", "nullable": True, "description": "Optional JSON metadata for documents."},
                        "documents": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": "Optional driving licence file (max 1).",
                        },
                        "profile_photo": {"type": "string", "format": "binary", "nullable": True},
                        "notes": {"type": "string", "nullable": True},
                    },
                    "patternProperties": {
                        "^capacity\\[[0-9]+\\]$": {"type": "string", "description": "Additional capacity values (VAN/TRUCK)."},
                    },
                    "required": [],
                }
            }
        }
    }
}

UPDATE_DRIVER_DRAFT = create_doc_entry(
    "Update a driver draft (multipart/form-data)",
    {
        200: success_entry(
            "Driver draft updated",
            data={
                "draft_id": "DF-001",
                "driver": {"id": "00000000-0000-0000-0000-000000000000", "account_status": "DRAFT"},
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission (DRIVERS write required)", code="FORBIDDEN", message="Not allowed"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Only DRAFT drivers can be updated via the drafts endpoint"),
    },
    description=(
        "Multipart endpoint for draft updates. Supports partial field updates plus optional profile photo "
        "and optional driving licence upsert. Requires at least one field or file. "
        "Requires Resource.DRIVERS WRITE (no driver document OTP for licence upload on this route). "
        "Response `driver.documents[].file_url` values are presigned when storage signing is configured."
    ),
)

UPDATE_DRIVER_DRAFT["openapi_extra"] = {
    "requestBody": {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "first_name": {"type": "string", "nullable": True},
                        "last_name": {"type": "string", "nullable": True},
                        "phone": {"type": "string", "nullable": True},
                        "email": {"type": "string", "nullable": True},
                        "driver_type": {"type": "string", "nullable": True},
                        "address_line1": {"type": "string", "nullable": True},
                        "address_line2": {"type": "string", "nullable": True},
                        "country": {"type": "string", "nullable": True},
                        "state": {"type": "string", "nullable": True},
                        "city": {"type": "string", "nullable": True},
                        "postcode": {"type": "string", "nullable": True},
                        "depot_id": {"type": "string", "nullable": True},
                        "vehicle_id": {"type": "string", "nullable": True},
                        "license_number": {"type": "string", "nullable": True},
                        "license_category": {"type": "string", "nullable": True},
                        "max_stops": {"type": "integer", "nullable": True},
                        "notes": {"type": "string", "nullable": True},
                        "okay_with_layover": {"type": "boolean", "nullable": True},
                        "layover_cost_per_night": {
                            "type": "string",
                            "nullable": True,
                            "description": "Optional GBP amount per night for draft overlay configuration.",
                        },
                        "max_layover_nights": {"type": "integer", "nullable": True},
                        "expected_version": {"type": "integer"},
                        "capacity[0]": {"type": "string", "description": "Optional first capacity value (VAN/TRUCK)."},
                        "profile_photo": {"type": "string", "format": "binary", "nullable": True},
                        "documents_metadata": {"type": "string", "nullable": True, "description": "Optional JSON metadata for documents."},
                        "documents": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": "Optional driving licence file (max 1).",
                        },
                    },
                    "patternProperties": {
                        "^capacity\\[[0-9]+\\]$": {"type": "string", "description": "Additional capacity values (VAN/TRUCK)."},
                    },
                    "required": ["expected_version"],
                }
            }
        }
    }
}

SUBMIT_DRIVER_DRAFT = create_doc_entry(
    "Submit a driver draft (finalize + activate)",
    {
        200: success_entry(
            "Driver draft submitted",
            data={
                "draft_id": "DF-001",
                "driver": {"id": "00000000-0000-0000-0000-000000000000", "account_status": "PENDING_ACTIVATION"},
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission (DRIVERS write required)", code="FORBIDDEN", message="Not allowed"),
        409: error_entry("Version conflict", code="CONFLICT", message="drivers was modified by another request."),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Only DRAFT drivers can be submitted"),
    },
    description=(
        "Multipart endpoint that enforces compulsory final fields (including operational layover configuration, "
        "same as ``POST /v1/drivers/add-new-driver``) and optionally upserts driving licence upload, "
        "then transitions the driver from DRAFT to PENDING_ACTIVATION and sends an activation email "
        "(deep link to set password; configure ``LINK_BASE_URL_DRIVER``). "
        "Requires Resource.DRIVERS WRITE (no driver document OTP for optional licence upload). "
        "Response `driver.documents[].file_url` values are presigned when storage signing is configured."
    ),
)

SUBMIT_DRIVER_DRAFT["openapi_extra"] = {
    "requestBody": {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string"},
                        "first_name": {"type": "string"},
                        "last_name": {"type": "string"},
                        "phone": {"type": "string"},
                        "driver_type": {"type": "string"},
                        "address_line1": {"type": "string"},
                        "address_line2": {"type": "string", "nullable": True},
                        "country": {"type": "string", "nullable": True},
                        "state": {"type": "string"},
                        "city": {"type": "string"},
                        "postcode": {"type": "string"},
                        "okay_with_layover": {"type": "boolean", "description": "Whether the driver accepts layovers."},
                        "layover_cost_per_night": {
                            "type": "string",
                            "description": "GBP per night (decimal string). Use 0 when not accepting layovers.",
                        },
                        "max_layover_nights": {"type": "integer", "description": "Maximum consecutive layover nights (0–366)."},
                        "latitude": {"type": "number", "nullable": True},
                        "longitude": {"type": "number", "nullable": True},
                        "depot_id": {"type": "string", "nullable": True},
                        "vehicle_id": {"type": "string", "nullable": True},
                        "license_number": {"type": "string", "nullable": True},
                        "license_category": {"type": "string", "nullable": True},
                        "max_stops": {"type": "integer"},
                        "capacity[0]": {"type": "string", "description": "Required first capacity value (VAN/TRUCK)."},
                        "documents_metadata": {"type": "string", "nullable": True, "description": "Optional JSON array metadata when `documents` is provided."},
                        "documents": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": "Optional single driving licence file (multipart field `documents`; max 1).",
                        },
                        "expected_version": {"type": "integer"},
                    },
                    "patternProperties": {
                        "^capacity\\[[0-9]+\\]$": {"type": "string", "description": "Additional capacity values (VAN/TRUCK)."},
                    },
                    "required": [
                        "email",
                        "first_name",
                        "last_name",
                        "phone",
                        "driver_type",
                        "address_line1",
                        "state",
                        "city",
                        "postcode",
                        "capacity[0]",
                        "okay_with_layover",
                        "layover_cost_per_night",
                        "max_layover_nights",
                        "expected_version",
                    ],
                }
            }
        }
    }
}

GET_DRIVER_KPIS = create_doc_entry(
    "Get driver KPIs / metrics (separate from list view)",
    {
        200: success_entry(
            "Driver KPIs",
            data={
                "total_employed": 120,
                "active_now": 87,
                "pending_activation": 5,
                "suspended": 3,
            },
        ),
        401: error_401_entry(),
        403: error_entry(
            "Insufficient permission (DRIVERS read required)",
            code="FORBIDDEN",
            message="Not allowed",
        ),
    },
    description=(
        "Requires Resource.DRIVERS READ. "
        "Returns KPI counts: "
        "`total_employed` matches GET /v1/drivers **without** filters (excludes `DRAFT`, requires linked user). "
        "`active_now`, `pending_activation`, and `suspended` are global linked-user totals by status."
    ),
)

GET_DRIVER = create_doc_entry(
    "Get driver by ID",
    {
        200: success_entry(
            "Driver detail with user info",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "user_id": "00000000-0000-0000-0000-000000000001",
                "driver_code": "DR-001",
                "user": {"id": "...", "email": "driver@example.com", "first_name": "Jane", "last_name": "Driver", "phone": "07123456789"},
                "depot_id": None,
                "vehicle_id": None,
                "address_line1": "879 South New Lane",
                "address_line2": "Flat 2",
                "capacities": ["VAN"],
                "driver_type": "INTERNAL",
                "country": "United Kingdom",
                "state": "Wales",
                "city": "Wrexham",
                "account_status": "ACTIVE",
                "live_status": "OFFLINE",
                "safety_score": 98,
                "on_time_deliveries": 230,
                "notes": None,
                "okay_with_layover": True,
                "layover_cost_per_night": "85.00",
                "max_layover_nights": 5,
                "profile_photo_url": "https://imagedelivery.net/.../public?expiry=...",
                "documents": {
                    "items": [
                        {
                            "id": "00000000-0000-0000-0000-000000000010",
                            "driver_id": "00000000-0000-0000-0000-000000000000",
                            "document_type": "DRIVING_LICENCE",
                            "title": "DRIVING LICENCE",
                            "file_url": None,
                            "expiry_date": "2030-01-01",
                            "status": "VALID",
                        }
                    ]
                },
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "version": 1,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
    description=(
        "Returns driver operational fields together with identity/contact fields resolved from the linked user profile "
        "(user_id relationship), full address information, profile photo URL, and documents. "
        "Requires Resource.DRIVERS READ only — **no** driver document OTP or `X-Driver-Doc-Access-Token`. "
        "Compliance `documents[].file_url` is always null here; presigned download URLs are only returned from "
        "driver document endpoints after OTP send/verify and `X-Driver-Doc-Access-Token`.\n\n"
        "**Operational configuration** is included on each driver: ``okay_with_layover``, ``layover_cost_per_night`` (GBP), "
        "and ``max_layover_nights``."
    ),
)

GET_DRIVER_CONFIGURATION = create_doc_entry(
    "Get driver operational configuration (layovers)",
    {
        200: success_entry(
            "Scheduling preferences only",
            data={
                "okay_with_layover": True,
                "layover_cost_per_night": "85.00",
                "max_layover_nights": 5,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
    description=(
        "Returns ``okay_with_layover``, ``layover_cost_per_night`` (GBP string), and ``max_layover_nights``. "
        "Requires Resource.DRIVERS READ. "
        "Use ``PATCH /v1/drivers/{driver_id}/configuration`` for modal saves."
    ),
)

PATCH_DRIVER_CONFIGURATION = create_doc_entry(
    "Update driver operational configuration",
    {
        200: success_entry(
            "Configuration saved",
            data={
                "okay_with_layover": True,
                "layover_cost_per_night": "85.00",
                "max_layover_nights": 5,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
        409: error_entry("Version conflict", code="CONFLICT", message="Driver was modified by another request."),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Invalid amounts"),
    },
    description=(
        "JSON body replaces operational scheduling preferences for the driver (same semantics as fields on "
        "``DriverDetailResponse``). Optional ``expected_version`` enables optimistic locking on the driver row. "
        "Requires Resource.DRIVERS WRITE."
    ),
)

GET_DRIVER_FULL = create_doc_entry(
    "Get full driver profile by ID",
    {
        200: success_entry(
            "Full driver profile with related resources",
            data={
                "driver": {
                    "id": "00000000-0000-0000-0000-000000000000",
                    "user_id": "00000000-0000-0000-0000-000000000001",
                    "driver_code": "DR-001",
                    "capacities": ["VAN", "TRUCK"],
                    "user": {"id": "...", "email": "driver@example.com", "first_name": "Jane", "last_name": "Driver", "phone": "07123456789"},
                    "profile_photo_url": "https://imagedelivery.net/.../public?expiry=...",
                    "address_line1": "879 South New Lane",
                    "address_line2": "Flat 2",
                    "country": "United Kingdom",
                    "state": "Wales",
                    "city": "Wrexham",
                    "postcode": "SW1A 1AA",
                    "account_status": "ACTIVE",
                    "live_status": "OFFLINE",
                    "safety_score": 98,
                    "on_time_deliveries": 230,
                    "okay_with_layover": True,
                    "layover_cost_per_night": "85.00",
                    "max_layover_nights": 5,
                },
                "documents": {
                    "items": [
                        {
                            "id": "00000000-0000-0000-0000-000000000000",
                            "driver_id": "00000000-0000-0000-0000-000000000001",
                            "document_type": "DRIVING_LICENCE",
                            "title": "DRIVING LICENCE",
                            "file_url": None,
                            "expiry_date": "2025-01-01",
                            "status": "VALID",
                        }
                    ]
                },
                "time_off": {
                    "items": [
                        {
                            "id": "00000000-0000-0000-0000-000000000000",
                            "driver_id": "00000000-0000-0000-0000-000000000001",
                            "start_date": "2024-01-01",
                            "end_date": "2024-01-05",
                            "type": "ANNUAL_LEAVE",
                            "days": 5,
                            "notes": "Family holiday",
                            "is_paid": True,
                        },
                        {
                            "id": "00000000-0000-0000-0000-000000000002",
                            "driver_id": "00000000-0000-0000-0000-000000000001",
                            "start_date": "2024-02-10",
                            "end_date": "2024-02-10",
                            "type": "SICK_LEAVE",
                            "days": 1,
                            "notes": "Flu",
                            "is_paid": True,
                        },
                    ],
                    "paid_leave_taken": 5,
                    "unpaid_leave_taken": 1,
                },
                "schedule": {
                    "days": [
                        {"day_of_week": 0, "is_active": True, "start_time": "08:00:00", "end_time": "17:00:00"},
                    ],
                    "total_weekly_hours": 40.0,
                },
                "shifts": {
                    "items": [],
                },
                "traffic_violations": {
                    "items": [],
                    "total": 0,
                    "page": 1,
                    "size": 50,
                },
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
    description=(
        "Convenience endpoint that aggregates driver detail, documents, unified time off (all leave types), "
        "weekly schedule, shifts, and traffic violations into a single payload. "
        "Identity fields inside the nested driver payload are sourced from the linked user profile. "
        "The nested driver object also includes operational configuration fields: ``okay_with_layover``, "
        "``layover_cost_per_night``, and ``max_layover_nights``. "
        "Intended for driver profile screens. Requires Resource.DRIVERS READ only — **no** driver document OTP or "
        "`X-Driver-Doc-Access-Token`. "
        "`documents[].file_url` is always null (use the driver document APIs with OTP + `X-Driver-Doc-Access-Token` "
        "for compliance file access). Traffic-violation proof `url` values are presigned download URLs, consistent "
        "with `GET /v1/drivers/{driver_id}/traffic-violations`."
    ),
)

CREATE_DRIVER = create_doc_entry(
    "Create a new driver (link to existing user)",
    {
        201: success_entry(
            "Driver created",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "user_id": "00000000-0000-0000-0000-000000000001",
                "driver_code": "DR-001",
                "first_name": "Jane",
                "last_name": "Driver",
                "email": "driver@example.com",
                "phone": "07123456789",
                "account_status": "ACTIVE",
                "live_status": "OFFLINE",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "version": 1,
            },
            message="Driver created",
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission (DRIVERS write required)", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("User not found", code="NOT_FOUND", message="user with id '...' not found"),
        409: error_entry("Driver already exists for this user", code="CONFLICT", message="A driver profile already exists for this user."),
    },
    description=("Legacy endpoint documentation retained for reference. " "The active creation flow is /add-new-driver. One driver per user. Requires Resource.DRIVERS WRITE."),
)

CREATE_DRIVER_WITH_USER = create_doc_entry(
    "Create user, driver, optional profile photo, and optional driving licence in one call",
    {
        201: success_entry(
            "User and driver created; optional photo/driving licence processed; credentials email sent",
            data={
                "driver": {
                    "id": "00000000-0000-0000-0000-000000000000",
                    "user_id": "00000000-0000-0000-0000-000000000001",
                    "driver_code": "DR-001",
                    "depot_id": None,
                    "vehicle_id": None,
                    "address_line1": "879 South New Lane",
                    "address_line2": "Flat 2",
                    "city": "Wrexham",
                    "postcode": "SW1A 1AA",
                    "user": {"id": "...", "email": "driver@example.com", "first_name": "Jane", "last_name": "Driver", "phone": "07123456789"},
                    "capacities": ["VAN", "TRUCK"],
                    "driver_type": "INTERNAL",
                    "country": "United Kingdom",
                    "state": "Wales",
                    "profile_photo_url": "https://imagedelivery.net/.../public?expiry=...",
                    "account_status": "PENDING_ACTIVATION",
                    "live_status": "OFFLINE",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "version": 1,
                },
                "documents": [
                    {
                        "type": "DRIVING_LICENCE",
                        "status": "success",
                        "error": None,
                    },
                ],
            },
            message="Driver and user created; activation email sent to driver.",
        ),
        400: error_entry(
            "Invalid registration data",
            code="VALIDATION_ERROR",
            message="Invalid email or document payload",
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission (DRIVERS write required)", code="FORBIDDEN", message="Not allowed"),
        409: error_entry("Email already exists", code="CONFLICT", message="A user with this email already exists."),
    },
    description=(
        "Multipart endpoint that creates a new user with role DRIVER and a linked driver profile. "
        "The driver completes onboarding via ``POST /v1/auth/driver-activation/set-password`` with header ``X-Invite-Token`` "
        "(token from the invite link; base URL ``LINK_BASE_URL_DRIVER``). JSON body must only contain `{ \"password\": \"...\" }`. "
        "Canonical identity fields: first_name, last_name, email, and phone. "
        "Capacities must be provided via indexed multipart fields: `capacity[0]` (required) and optional `capacity[1]`/... (VAN/TRUCK). "
        "Response includes `capacities` (authoritative list). "
        "Requires Resource.DRIVERS WRITE.\n\n"
        "This endpoint is **final submit only** and always creates the driver as PENDING_ACTIVATION (the driver becomes ACTIVE on first login).\n\n"
        "Optional `profile_photo`: multipart file (JPEG/PNG, max 5MB), stored via Cloudflare Images; "
        "response `driver.profile_photo_url` is a signed URL when configured.\n\n"
        "Driving licence is optional. When provided, send exactly one file in multipart field `documents` with "
        "`documents_metadata` as a JSON array of one object (index-aligned).\n"
        "`document_type` must be `DRIVING_LICENCE`. `title` is optional and must match the canonical display title if provided. "
        "`expiry_date` (YYYY-MM-DD) is required and cannot be in the past. "
        "Custom or other document types are rejected here — use POST /v1/drivers/{driver_id}/documents after creation.\n\n"
        "**Scheduling configuration (required):** ``okay_with_layover`` (bool), ``layover_cost_per_night`` (decimal string, GBP per night), "
        "and ``max_layover_nights`` (0–366).\n\n"
        "The documents array in the response lists per-upload results (type, status success|failed, optional error). "
        "Driver row is created if core validation passes; licence upload failures appear in documents[]; "
        "invalid profile photo types raise validation before completion. "
        "Optional licence upload does not require driver document OTP (OTP is only for GET document list/detail with `file_url`)."
    ),
)

# FastAPI can't infer dynamic indexed multipart field names (capacity[0], capacity[1], ...),
# because they are parsed from request.form() rather than declared parameters.
# Provide a minimal requestBody schema so Swagger UI displays the expected fields.
CREATE_DRIVER_WITH_USER["openapi_extra"] = {
    "requestBody": {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string"},
                        "first_name": {"type": "string"},
                        "last_name": {"type": "string"},
                        "phone": {"type": "string"},
                        "driver_type": {"type": "string"},
                        "address_line1": {"type": "string"},
                        "address_line2": {"type": "string", "nullable": True},
                        "country": {"type": "string", "nullable": True},
                        "state": {"type": "string"},
                        "city": {"type": "string"},
                        "postcode": {"type": "string"},
                        "latitude": {"type": "number", "nullable": True},
                        "longitude": {"type": "number", "nullable": True},
                        "depot_id": {"type": "string", "nullable": True},
                        "vehicle_id": {"type": "string", "nullable": True},
                        "license_number": {"type": "string", "nullable": True},
                        "license_category": {"type": "string", "nullable": True},
                        "max_stops": {"type": "integer"},
                        "okay_with_layover": {"type": "boolean", "description": "Required — accepts layovers"},
                        "layover_cost_per_night": {
                            "type": "string",
                            "description": "Required GBP amount per night (e.g. 85). Use 0 if okay_with_layover is false.",
                        },
                        "max_layover_nights": {"type": "integer", "description": "Required maximum consecutive layover nights (0–366)."},
                        "capacity[0]": {
                            "type": "string",
                            "description": "Required first capacity value (VAN/TRUCK).",
                        },
                        "documents_metadata": {"type": "string", "nullable": True, "description": "Optional JSON array metadata when `documents` is provided."},
                        "documents": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": "Optional single driving licence file (multipart field `documents`; max 1).",
                        },
                        "profile_photo": {"type": "string", "format": "binary", "nullable": True},
                        "notes": {"type": "string", "nullable": True},
                    },
                    "patternProperties": {
                        "^capacity\\[[0-9]+\\]$": {"type": "string", "description": "Additional capacity values (VAN/TRUCK)."},
                    },
                    "required": [
                        "email",
                        "first_name",
                        "last_name",
                        "phone",
                        "driver_type",
                        "address_line1",
                        "state",
                        "city",
                        "postcode",
                        "okay_with_layover",
                        "layover_cost_per_night",
                        "max_layover_nights",
                        "capacity[0]",
                    ],
                }
            }
        }
    }
}

UPDATE_DRIVER = create_doc_entry(
    "Update driver by ID",
    {
        200: success_entry(
            "Driver updated",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "user_id": "...",
                "driver_code": "DR-001",
                "user": {"id": "...", "email": "driver@example.com", "first_name": "Jane", "last_name": "Driver", "phone": "07123456789"},
                "depot_id": None,
                "vehicle_id": None,
                "address_line1": "879 South New Lane",
                "address_line2": "Flat 2",
                "city": "Wrexham",
                "postcode": "SW1A 1AA",
                "capacities": ["VAN"],
                "driver_type": "INTERNAL",
                "country": "United Kingdom",
                "state": "Wales",
                "account_status": "ACTIVE",
                "live_status": "OFFLINE",
                "profile_photo_url": "https://imagedelivery.net/.../public?expiry=...",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "version": 2,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
        409: error_entry("Version conflict", code="CONFLICT", message="Driver was modified by another request."),
    },
    description=(
        "Partial update. Pass expected_version for optimistic locking. "
        "Identity fields (first_name, last_name, email, phone) update the linked user profile; "
        "driver-specific fields update the driver profile (including country/state/city). "
        "Operational fields ``okay_with_layover``, ``layover_cost_per_night``, and ``max_layover_nights`` "
        "may also be updated here, or via ``PATCH /v1/drivers/{driver_id}/configuration``. "
        "Requires Resource.DRIVERS WRITE."
    ),
)

UPDATE_DRIVER_FORM = create_doc_entry(
    "Update driver by ID (multipart/form-data)",
    {
        200: success_entry(
            "Driver updated",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "user_id": "...",
                "driver_code": "DR-001",
                "user": {"id": "...", "email": "driver@example.com", "first_name": "Jane", "last_name": "Driver", "phone": "07123456789"},
                "depot_id": None,
                "vehicle_id": None,
                "address_line1": "879 South New Lane",
                "address_line2": "Flat 2",
                "capacities": ["VAN"],
                "driver_type": "INTERNAL",
                "country": "United Kingdom",
                "state": "Wales",
                "city": "Wrexham",
                "postcode": "SW1A 1AA",
                "account_status": "ACTIVE",
                "live_status": "OFFLINE",
                "profile_photo_url": "https://imagedelivery.net/.../public?expiry=...",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "version": 2,
            },
            message="profile photo updated; driving licence replaced",
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
        409: error_entry("Version conflict", code="CONFLICT", message="Driver was modified by another request."),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Invalid multipart form fields"),
    },
    description=(
        "Multipart partial update for driver profile fields. "
        "Supports optional file uploads: profile_photo and driving_licence_file "
        "(with required driving_licence_expiry_date). "
        "Capacities can be provided as capacity[0], capacity[1], ... or repeated capacities[] keys. "
        "When a file upload succeeds, the response includes a `message` indicating which upload(s) were applied. "
        "Licence / `documents[]` uploads do not require driver document OTP (OTP is only for GET document list/detail with `file_url`)."
    ),
)

UPDATE_DRIVER_FORM["openapi_extra"] = {
    "requestBody": {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "first_name": {"type": "string"},
                        "last_name": {"type": "string"},
                        "phone": {"type": "string"},
                        "email": {"type": "string"},
                        "driver_type": {"type": "string"},
                        "address_line1": {"type": "string"},
                        "address_line2": {"type": "string"},
                        "country": {"type": "string"},
                        "state": {"type": "string"},
                        "city": {"type": "string"},
                        "postcode": {"type": "string"},
                        "depot_id": {"type": "string"},
                        "vehicle_id": {"type": "string"},
                        "license_number": {"type": "string"},
                        "license_category": {"type": "string"},
                        "max_stops": {"type": "integer"},
                        "account_status": {"type": "string"},
                        "live_status": {"type": "string"},
                        "notes": {"type": "string"},
                        "expected_version": {"type": "integer"},
                        "capacity[0]": {"type": "string"},
                        "profile_photo": {"type": "string", "format": "binary"},
                        "driving_licence_file": {"type": "string", "format": "binary"},
                        "driving_licence_expiry_date": {"type": "string", "format": "date"},
                        "documents_metadata": {
                            "type": "string",
                            "description": "Alternative to driving_licence_* fields: JSON array with exactly one object: {document_type: DRIVING_LICENCE, expiry_date: YYYY-MM-DD}.",
                        },
                        "documents": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": "Alternative to driving_licence_file: send exactly one file as documents[0].",
                        },
                    },
                    "patternProperties": {
                        "^capacity\\[[0-9]+\\]$": {"type": "string"},
                    },
                }
            }
        }
    }
}

DELETE_DRIVER = create_doc_entry(
    "Hard-delete driver (permanent)",
    {
        200: success_entry(
            "Driver deleted",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "user_id": "00000000-0000-0000-0000-000000000001",
                "driver_code": "DR-001",
                "user": {"id": "...", "email": "driver@example.com", "first_name": "Jane", "last_name": "Driver", "phone": "07123456789"},
                "depot_id": None,
                "vehicle_id": None,
                "address_line1": "879 South New Lane",
                "address_line2": "Flat 2",
                "city": "Wrexham",
                "postcode": "SW1A 1AA",
                "capacities": ["VAN"],
                "driver_type": "INTERNAL",
                "country": "United Kingdom",
                "state": "Wales",
                "account_status": "ACTIVE",
                "live_status": "OFFLINE",
                "profile_photo_url": "https://imagedelivery.net/.../public?expiry=...",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "version": 2,
            },
            message="Driver deleted",
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
    description="Hard-deletes driver + linked user; best-effort deletes external files (R2/Cloudflare). Requires Resource.DRIVERS WRITE.",
)

DELETE_DRIVER_DRAFT = create_doc_entry(
    "Delete draft driver by draft id",
    {
        200: success_entry(
            "Driver draft deleted",
            data={"message": "Driver draft deleted"},
            message="Driver draft deleted",
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Draft not found", code="NOT_FOUND", message="driver_draft with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Only non-submitted DRAFT drivers can be deleted",
        ),
    },
    description="Hard-deletes a non-submitted draft and best-effort cleans related files from R2/Cloudflare.",
)

CREATE_DRIVER_ONBOARDING = create_doc_entry(
    "Onboard driver with initial documents",
    {
        201: success_entry(
            "Driver onboarded",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_code": "DR-001",
                "first_name": "Jane",
                "last_name": "Driver",
                "account_status": "ACTIVE",
            },
            message="Driver onboarded",
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission (DRIVERS write required)", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("User not found", code="NOT_FOUND", message="user with id '...' not found"),
        409: error_entry("Driver already exists for this user", code="CONFLICT", message="A driver profile already exists for this user."),
    },
    description=(
        "Legacy onboarding documentation retained for reference. "
        "Active creation flow is /add-new-driver. "
        "Identity/contact fields in responses come from the linked user profile. Requires Resource.DRIVERS WRITE."
    ),
)

# Swagger UI: required step-up header on compliance `/documents` routes (org docs document the same pattern for `X-Doc-Access-Token`).
_DRIVER_COMPLIANCE_DOCUMENTS_OPENAPI_EXTRA: dict[str, Any] = {
    "parameters": [
        {
            "name": "X-Driver-Doc-Access-Token",
            "in": "header",
            "required": True,
            "schema": {"type": "string"},
            "description": (
                "64-char hex from `POST /v1/drivers/documents/otp/verify`. "
                "Required with JWT on every driver compliance document route under `/v1/drivers/.../documents`. "
                "Not interchangeable with organisation `X-Doc-Access-Token`."
            ),
        },
    ],
}

_DRIVER_DOC_STEPUP_VS_ORG = (
    "\n\n**vs organisation documents:** Org uses **`X-Doc-Access-Token`** from "
    "`POST /v1/organizations/documents/otp/send` → `.../otp/verify`. Driver uses **`X-Driver-Doc-Access-Token`** "
    "from `POST /v1/drivers/documents/otp/send` → `.../otp/verify`. Tokens and OTP rate limits are **per scope** "
    "(mixing org OTP on driver routes or vice versa returns 401). "
    "Org step-up applies to most org document and share APIs (including uploads); driver step-up applies **only** to "
    "`GET|POST /v1/drivers/{driver_id}/documents`, `GET .../documents/{id}/full`, and `PATCH|DELETE .../documents/{id}`. "
    "Org **`POST /v1/organizations/{org_id}/contract`** currently does **not** use `DocAccessDep`."
)

LIST_DRIVER_TERMS = create_doc_entry(
    "Get driver terms and conditions config",
    {
        200: success_entry(
            "Terms list",
            data={
                "items": [
                    {
                        "id": "00000000-0000-0000-0000-000000000123",
                        "title": "SW Couriers Driver Terms and Conditions",
                        "clauses": [
                            {
                                "clause_order": 1,
                                "heading": "Acceptance of Terms",
                                "body": "By accessing and using this application...",
                            }
                        ],
                        "is_active": True,
                        "effective_from": "2026-04-01T00:00:00Z",
                        "created_at": "2026-04-01T00:00:00Z",
                        "updated_at": "2026-04-01T00:00:00Z",
                    }
                ]
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
    },
)

CREATE_DRIVER_TERMS = create_doc_entry(
    "Create driver terms and conditions config",
    {
        201: success_entry(
            "Terms created",
            data={
                "id": "00000000-0000-0000-0000-000000000123",
                "title": "SW Couriers Driver Terms and Conditions",
                "clauses": [
                    {
                        "clause_order": 1,
                        "heading": "Acceptance of Terms",
                        "body": "By accessing and using this application...",
                    }
                ],
                "is_active": True,
                "effective_from": "2026-05-01T00:00:00Z",
                "created_at": "2026-04-09T00:00:00Z",
                "updated_at": "2026-04-09T00:00:00Z",
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Invalid input"),
    },
    description="Admin endpoint to create/update the terms config content.",
)

UPDATE_DRIVER_TERMS = create_doc_entry(
    "Update driver terms and conditions config",
    {
        200: success_entry(
            "Terms updated",
            data={
                "id": "00000000-0000-0000-0000-000000000123",
                "title": "Updated Terms",
                "clauses": [
                    {
                        "clause_order": 1,
                        "heading": "Acceptance of Terms",
                        "body": "Updated clause body",
                    }
                ],
                "is_active": False,
                "effective_from": "2026-05-01T00:00:00Z",
                "created_at": "2026-04-09T00:00:00Z",
                "updated_at": "2026-04-09T00:10:00Z",
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Terms not found", code="NOT_FOUND", message="driver_terms_and_conditions with id '...' not found"),
    },
)

SEND_DRIVER_DOC_OTP = create_doc_entry(
    "Request a driver document access OTP",
    {
        200: success_entry(
            "OTP sent",
            data={"message": "OTP sent to your registered email address. It expires in 10 minutes."},
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Insufficient permission (Resource.DRIVERS WRITE required)",
            code="FORBIDDEN",
            message="Not allowed",
        ),
        422: error_entry(
            "Application rate limit: too many OTP sends for this user in the driver-doc scope",
            code="VALIDATION_ERROR",
            message="Too many OTP requests. Maximum 3 per 10 minutes. Please wait and try again.",
        ),
        429: error_entry(
            "HTTP rate limit (SlowAPI / shared drivers-write bucket)",
            code="RATE_LIMIT_EXCEEDED",
            message="Too many requests. Please try again later.",
        ),
    },
    description=(
        "**Step-up (email OTP)** for **driver compliance documents** under `/v1/drivers/.../documents` "
        "(list, get, upload, update, delete). Sends a 6-digit code to the "
        "authenticated user's registered email (`access_scope=DRIVER_DOCUMENTS`). This flow is separate from "
        "organisation document OTPs: org OTP counts and tokens do **not** apply here.\n\n"
        "**Not in scope:** Licence uploads on drafts, add-new-driver, and `PATCH .../form` do **not** require this header. "
        "Traffic-violation APIs, activity log, and read-only `GET /v1/drivers/{id}` / `.../full` also do **not**.\n\n"
        "**Auth:** Requires `Resource.DRIVERS` **WRITE** (stricter than org document OTP, which only needs a valid session).\n\n"
        "**Limits:** (1) At most **3** OTP emails per user per **10 minutes** for this scope. "
        "(2) This route shares the drivers **write** HTTP rate limit with other driver mutation endpoints.\n\n"
        "**OTP:** Expires in **10 minutes**. Does **not** require `X-Driver-Doc-Access-Token`.\n\n"
        "**Next:** `POST /v1/drivers/documents/otp/verify` → receive `driver_doc_access_token` for header "
        "`X-Driver-Doc-Access-Token` (1 hour).\n\n"
        "```\n"
        "curl -X POST http://localhost:8000/v1/drivers/documents/otp/send \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Client-Type: ADMIN"\n'
        "```"
        f"{_DRIVER_DOC_STEPUP_VS_ORG}"
    ),
)

VERIFY_DRIVER_DOC_OTP = create_doc_entry(
    "Verify OTP and receive a driver document access token",
    {
        200: success_entry(
            "OTP verified — driver doc access token issued",
            data={
                "driver_doc_access_token": "a3f1c2e4b5d6..." * 4,
                "expires_in": 3600,
                "expires_at": "2026-04-01T13:00:00Z",
                "message": "OTP verified. Use X-Driver-Doc-Access-Token on all /v1/drivers/.../documents routes.",
            },
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired OTP"),
        403: error_entry(
            "Insufficient permission (Resource.DRIVERS WRITE required)",
            code="FORBIDDEN",
            message="Not allowed",
        ),
        422: error_entry(
            "Request body validation (OTP must be exactly 6 digits)",
            code="VALIDATION_ERROR",
            message="OTP must be exactly 6 digits",
        ),
        429: error_entry(
            "OTP verify rate limit or lockout",
            code="RATE_LIMIT_EXCEEDED",
            message="Too many verify attempts. Please try again later or request a new OTP.",
        ),
    },
    description=(
        "Validates a **driver-scope** OTP (`DRIVER_DOCUMENTS`) from email, marks it used, and returns "
        "`driver_doc_access_token` (64-char hex). Pass it as **`X-Driver-Doc-Access-Token`** on every driver compliance "
        "document route: **`GET|POST /v1/drivers/{driver_id}/documents`**, **`GET /v1/drivers/documents/{document_id}/full`**, "
        "**`PATCH|DELETE /v1/drivers/documents/{document_id}`**. "
        "Do **not** send it for draft/add-new/form licence uploads, traffic violations, activity log, "
        "or plain `GET` driver profile calls. "
        "Token TTL: **1 hour**. OTPs are **single-use** and expire after **10 minutes**. "
        "Verify is rate-limited per IP; repeated invalid attempts per user may trigger a temporary lockout.\n\n"
        "**Auth:** Same as send — `Resource.DRIVERS` **WRITE**. This request does **not** require "
        "`X-Driver-Doc-Access-Token`.\n\n"
        "**Scope:** Codes from the **organisation** document OTP flow will **not** work here (wrong `access_scope`).\n\n"
        "```\n"
        "curl -X POST http://localhost:8000/v1/drivers/documents/otp/verify \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Client-Type: ADMIN" \\\n'
        '  -H "Content-Type: application/json" \\\n'
        "  -d '{\"otp\": \"123456\"}'\n"
        "```"
        f"{_DRIVER_DOC_STEPUP_VS_ORG}"
    ),
)

DOCUMENTS_LIST = create_doc_entry(
    "List driver documents",
    {
        200: success_entry(
            "Documents list (each item has file_url for preview and status: VALID, EXPIRING_SOON, or EXPIRED)",
            data={
                "items": [
                    {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "driver_id": "00000000-0000-0000-0000-000000000001",
                        "document_type": "DRIVING_LICENCE",
                        "title": "DRIVING LICENCE",
                        "file_url": "drivers/driver-id/compliance/file-key",
                        "expiry_date": "2025-01-01",
                        "status": "VALID",
                    }
                ]
            },
        ),
        401: error_401_entry(
            "AUTHENTICATION_ERROR",
            "Invalid or expired access token or missing X-Driver-Doc-Access-Token",
        ),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
    description=(
        "Each document includes file_url (preview/download) and status (auto-calculated: VALID, EXPIRING_SOON ≤30 days, EXPIRED).\n\n"
        "**Step-up:** Requires a valid **`X-Driver-Doc-Access-Token`** from "
        "`POST /v1/drivers/documents/otp/verify` plus normal JWT (same header on upload/update/delete document routes). "
        "Organisation document tokens (`X-Doc-Access-Token`) are **not** accepted.\n\n"
        "```\n"
        "curl \"http://localhost:8000/v1/drivers/<driver_id>/documents\" \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Client-Type: ADMIN" \\\n'
        '  -H "X-Driver-Doc-Access-Token: <driver_doc_access_token>"\n'
        "```"
    ),
)

DOCUMENTS_MUTATE = create_doc_entry(
    "Upload driver document",
    {
        201: success_entry(
            "Document uploaded",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "document_type": "DRIVING_LICENCE",
                "title": "DRIVING LICENCE",
                "file_url": "drivers/driver-id/compliance/file-key",
                "expiry_date": "2025-01-01",
                "status": "VALID",
            },
        ),
        401: error_401_entry(
            "AUTHENTICATION_ERROR",
            "Invalid or expired access token or missing X-Driver-Doc-Access-Token",
        ),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Invalid file type or size"),
    },
    description=(
        "Multipart: document_type (enum: DRIVING_LICENCE, CUSTOM), "
        "title (required for CUSTOM; for other types derived from enum), expiry_date, file. "
        "Response includes file_url (preview) and status (VALID, EXPIRING_SOON, EXPIRED). "
        "Requires `Resource.DRIVERS` WRITE and **`X-Driver-Doc-Access-Token`** from the driver document OTP flow.\n\n"
        "```\n"
        "curl -X POST \"http://localhost:8000/v1/drivers/<driver_id>/documents\" \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Client-Type: ADMIN" \\\n'
        '  -H "X-Driver-Doc-Access-Token: <driver_doc_access_token>" \\\n'
        '  -F "file=@/path/to/doc.png;type=image/png" \\\n'
        '  -F "document_type=CUSTOM" \\\n'
        '  -F "title=Induction certificate" \\\n'
        '  -F "expiry_date=2030-12-31"\n'
        "```"
    ),
)

DOCUMENT_GET_FULL = create_doc_entry(
    "Get driver document by ID",
    {
        200: success_entry(
            "Driver document with file_url (preview) and auto-calculated status",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "document_type": "DRIVING_LICENCE",
                "title": "DRIVING LICENCE",
                "file_url": "drivers/driver-id/compliance/file-key",
                "expiry_date": "2025-01-01",
                "status": "VALID",
            },
        ),
        401: error_401_entry(
            "AUTHENTICATION_ERROR",
            "Invalid or expired access token or missing X-Driver-Doc-Access-Token",
        ),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Document not found", code="NOT_FOUND", message="document with id '...' not found"),
    },
    description=(
        "Returns document with file_url for preview/download and status: VALID, EXPIRING_SOON (≤30 days), or EXPIRED. "
        "Requires `X-Driver-Doc-Access-Token` from the driver document OTP flow (not org `X-Doc-Access-Token`).\n\n"
        "```\n"
        "curl \"http://localhost:8000/v1/drivers/documents/<document_id>/full\" \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Client-Type: ADMIN" \\\n'
        '  -H "X-Driver-Doc-Access-Token: <driver_doc_access_token>"\n'
        "```"
    ),
)

DOCUMENT_UPDATE = create_doc_entry(
    "Update driver document metadata and/or file",
    {
        200: success_entry(
            "Document updated (with file_url for preview and auto-calculated status)",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "document_type": "DRIVING_LICENCE",
                "title": "DRIVING LICENCE",
                "file_url": "drivers/driver-id/compliance/file-key",
                "expiry_date": "2025-01-01",
                "status": "VALID",
            },
        ),
        401: error_401_entry(
            "AUTHENTICATION_ERROR",
            "Invalid or expired access token or missing X-Driver-Doc-Access-Token",
        ),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Document not found", code="NOT_FOUND", message="document with id '...' not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Invalid expiry date or file type/size"),
    },
    description=(
        "Optional: new file (image/PDF), title (CUSTOM only), expiry_date. Response includes file_url (preview) and status: "
        "VALID, EXPIRING_SOON (≤30 days), or EXPIRED. Requires `Resource.DRIVERS` WRITE and **`X-Driver-Doc-Access-Token`**.\n\n"
        "```\n"
        "curl -X PATCH \"http://localhost:8000/v1/drivers/documents/<document_id>\" \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Client-Type: ADMIN" \\\n'
        '  -H "X-Driver-Doc-Access-Token: <driver_doc_access_token>" \\\n'
        '  -F "expiry_date=2031-06-01"\n'
        "```"
    ),
)

DOCUMENT_DELETE = create_doc_entry(
    "Delete driver document",
    {
        200: success_entry(
            "Document deleted",
            data={},
        ),
        401: error_401_entry(
            "AUTHENTICATION_ERROR",
            "Invalid or expired access token or missing X-Driver-Doc-Access-Token",
        ),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Document not found", code="NOT_FOUND", message="document with id '...' not found"),
    },
    description=(
        "Hard-delete a compliance document. Requires `Resource.DRIVERS` WRITE and **`X-Driver-Doc-Access-Token`**.\n\n"
        "```\n"
        "curl -X DELETE \"http://localhost:8000/v1/drivers/documents/<document_id>\" \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Client-Type: ADMIN" \\\n'
        '  -H "X-Driver-Doc-Access-Token: <driver_doc_access_token>"\n'
        "```"
    ),
)

DOCUMENTS_LIST["openapi_extra"] = _DRIVER_COMPLIANCE_DOCUMENTS_OPENAPI_EXTRA
DOCUMENTS_MUTATE["openapi_extra"] = _DRIVER_COMPLIANCE_DOCUMENTS_OPENAPI_EXTRA
DOCUMENT_GET_FULL["openapi_extra"] = _DRIVER_COMPLIANCE_DOCUMENTS_OPENAPI_EXTRA
DOCUMENT_UPDATE["openapi_extra"] = _DRIVER_COMPLIANCE_DOCUMENTS_OPENAPI_EXTRA
DOCUMENT_DELETE["openapi_extra"] = _DRIVER_COMPLIANCE_DOCUMENTS_OPENAPI_EXTRA

# Draft document docs (reuse compliance openapi_extra since same scope and header)
DRAFT_DOCUMENTS_LIST = create_doc_entry(
    "List draft driver documents",
    {
        200: success_entry(
            "Documents list (each item has file_url for preview and status: VALID, EXPIRING_SOON, or EXPIRED)",
            data={
                "items": [
                    {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "driver_id": "00000000-0000-0000-0000-000000000001",
                        "document_type": "DRIVING_LICENCE",
                        "title": "DRIVING LICENCE",
                        "file_url": "drivers/driver-id/compliance/file-key",
                        "expiry_date": "2025-01-01",
                        "status": "VALID",
                    }
                ]
            },
        ),
        401: error_401_entry(
            "AUTHENTICATION_ERROR",
            "Invalid or expired access token or missing X-Driver-Doc-Access-Token",
        ),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver draft not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
    description=(
        "List documents for a draft driver (same as compliance documents but for drafts).\n\n"
        "**Step-up:** Requires a valid **`X-Driver-Doc-Access-Token`** from "
        "`POST /v1/drivers/documents/otp/verify` plus normal JWT. "
        "Organisation document tokens (`X-Doc-Access-Token`) are **not** accepted.\n\n"
        "```\n"
        "curl \"http://localhost:8000/v1/drivers/drafts/<draft_id>/documents\" \\\n"
        '  -H "Authorization: Bearer <your_token>" \\\n'
        '  -H "X-Client-Type: ADMIN" \\\n'
        '  -H "X-Driver-Doc-Access-Token: <driver_doc_access_token>"\n'
        "```"
    ),
)

DRAFT_DOCUMENTS_MUTATE = create_doc_entry(
    "Upload draft driver document",
    {
        201: success_entry(
            "Document uploaded",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "document_type": "DRIVING_LICENCE",
                "title": "DRIVING LICENCE",
                "file_url": "drivers/driver-id/compliance/file-key",
                "expiry_date": "2025-01-01",
                "status": "VALID",
            },
        ),
        401: error_401_entry(
            "AUTHENTICATION_ERROR",
            "Invalid or expired access token or missing X-Driver-Doc-Access-Token",
        ),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver draft not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
    description=(
        "Upload a new document for a draft driver (same as compliance but for drafts).\n\n"
        "**Step-up:** Requires **`X-Driver-Doc-Access-Token`**."
    ),
)

DRAFT_DOCUMENT_GET_FULL = create_doc_entry(
    "Get draft driver document full",
    {
        200: success_entry(
            "Document details",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "document_type": "DRIVING_LICENCE",
                "title": "DRIVING LICENCE",
                "file_url": "drivers/driver-id/compliance/file-key",
                "expiry_date": "2025-01-01",
                "status": "VALID",
            },
        ),
        401: error_401_entry(
            "AUTHENTICATION_ERROR",
            "Invalid or expired access token or missing X-Driver-Doc-Access-Token",
        ),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Document not found", code="NOT_FOUND", message="document with id '...' not found"),
    },
    description=(
        "Get full details of a draft driver document.\n\n"
        "**Step-up:** Requires **`X-Driver-Doc-Access-Token`**."
    ),
)

DRAFT_DOCUMENT_UPDATE = create_doc_entry(
    "Update draft driver document",
    {
        200: success_entry(
            "Document updated",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "document_type": "DRIVING_LICENCE",
                "title": "DRIVING LICENCE",
                "file_url": "drivers/driver-id/compliance/file-key",
                "expiry_date": "2025-01-01",
                "status": "VALID",
            },
        ),
        401: error_401_entry(
            "AUTHENTICATION_ERROR",
            "Invalid or expired access token or missing X-Driver-Doc-Access-Token",
        ),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Document not found", code="NOT_FOUND", message="document with id '...' not found"),
    },
    description=(
        "Update metadata and/or file for a draft driver document.\n\n"
        "**Step-up:** Requires **`X-Driver-Doc-Access-Token`**."
    ),
)

DRAFT_DOCUMENT_DELETE = create_doc_entry(
    "Delete draft driver document",
    {
        200: success_entry("Document deleted", data={}),
        401: error_401_entry(
            "AUTHENTICATION_ERROR",
            "Invalid or expired access token or missing X-Driver-Doc-Access-Token",
        ),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Document not found", code="NOT_FOUND", message="document with id '...' not found"),
    },
    description=(
        "Delete a draft driver document.\n\n"
        "**Step-up:** Requires **`X-Driver-Doc-Access-Token`**."
    ),
)

DRAFT_DOCUMENTS_LIST["openapi_extra"] = _DRIVER_COMPLIANCE_DOCUMENTS_OPENAPI_EXTRA
DRAFT_DOCUMENTS_MUTATE["openapi_extra"] = _DRIVER_COMPLIANCE_DOCUMENTS_OPENAPI_EXTRA
DRAFT_DOCUMENT_GET_FULL["openapi_extra"] = _DRIVER_COMPLIANCE_DOCUMENTS_OPENAPI_EXTRA
DRAFT_DOCUMENT_UPDATE["openapi_extra"] = _DRIVER_COMPLIANCE_DOCUMENTS_OPENAPI_EXTRA
DRAFT_DOCUMENT_DELETE["openapi_extra"] = _DRIVER_COMPLIANCE_DOCUMENTS_OPENAPI_EXTRA

TIME_OFF_LIST = create_doc_entry(
    "List driver time off and KPIs",
    {
        200: success_entry(
            "Time off entries",
            data={
                "items": [
                    {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "driver_id": "00000000-0000-0000-0000-000000000001",
                        "start_date": "2026-01-01",
                        "end_date": "2026-01-05",
                        "type": "ANNUAL_LEAVE",
                        "days": 5,
                        "notes": "Family holiday",
                        "is_paid": True,
                    },
                    {
                        "id": "00000000-0000-0000-0000-000000000002",
                        "driver_id": "00000000-0000-0000-0000-000000000001",
                        "start_date": "2026-02-10",
                        "end_date": "2026-02-12",
                        "type": "SICK_LEAVE",
                        "days": 3,
                        "notes": "Flu",
                        "is_paid": False,
                    },
                ],
                "paid_leave_taken": 5,
                "unpaid_leave_taken": 3,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
    description=(
        "Requires Resource.DRIVERS READ. Returns all time-off entries for the driver across all TimeOffType values "
        "(e.g. ANNUAL_LEAVE, SICK_LEAVE, MEDICAL_APPOINTMENT, etc.). No entitlement or remaining-balance limits are enforced. "
        "Also returns KPIs for the current calendar year: paid_leave_taken (sum of days for is_paid=true) and "
        "unpaid_leave_taken (sum of days for is_paid=false)."
    ),
)

TIME_OFF_MUTATE = create_doc_entry(
    "Create driver time off",
    {
        201: success_entry(
            "Time off created",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "start_date": "2024-01-01",
                "end_date": "2024-01-05",
                "type": "ANNUAL_LEAVE",
                "days": 5,
                "notes": "Family holiday",
                "is_paid": True,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver/time off not found", code="NOT_FOUND", message="Resource not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Invalid date range",
        ),
    },
)

TIME_OFF_GET_FULL = create_doc_entry(
    "Get driver time off by ID",
    {
        200: success_entry(
            "Time off entry",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "start_date": "2024-01-01",
                "end_date": "2024-01-05",
                "type": "ANNUAL_LEAVE",
                "days": 5,
                "notes": "Family holiday",
                "is_paid": True,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver/time off not found", code="NOT_FOUND", message="Resource not found"),
    },
)

TIME_OFF_UPDATE = create_doc_entry(
    "Update driver time off",
    {
        200: success_entry(
            "Time off updated",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "start_date": "2024-01-01",
                "end_date": "2024-01-05",
                "type": "ANNUAL_LEAVE",
                "days": 5,
                "notes": "Family holiday",
                "is_paid": True,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver/time off not found", code="NOT_FOUND", message="Resource not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Invalid date range or requested time off exceeds remaining balance for this leave type",
        ),
    },
)

TIME_OFF_DELETE = create_doc_entry(
    "Delete driver time off",
    {
        200: success_entry(
            "Time off deleted",
            data={},
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver/time off not found", code="NOT_FOUND", message="Resource not found"),
    },
)

SUSPEND_DRIVER = create_doc_entry(
    "Suspend driver account",
    {
        200: success_entry(
            "Driver suspended",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "user_id": "00000000-0000-0000-0000-000000000001",
                "driver_code": "DR-001",
                "user": {"id": "...", "email": "driver@example.com", "first_name": "Jane", "last_name": "Driver", "phone": "07123456789"},
                "depot_id": None,
                "vehicle_id": None,
                "address_line1": "879 South New Lane",
                "address_line2": "Flat 2",
                "city": "Wrexham",
                "postcode": "SW1A 1AA",
                "capacities": ["VAN"],
                "driver_type": "INTERNAL",
                "country": "United Kingdom",
                "state": "Wales",
                "account_status": "SUSPENDED",
                "live_status": "OFFLINE",
                "profile_photo_url": "https://imagedelivery.net/.../public?expiry=...",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "version": 2,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
        409: error_entry("Invalid state transition", code="INVALID_STATE_TRANSITION", message="Cannot transition driver from current state to SUSPENDED"),
    },
)

REACTIVATE_DRIVER = create_doc_entry(
    "Reactivate driver account",
    {
        200: success_entry(
            "Driver reactivated",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "user_id": "00000000-0000-0000-0000-000000000001",
                "driver_code": "DR-001",
                "user": {"id": "...", "email": "driver@example.com", "first_name": "Jane", "last_name": "Driver", "phone": "07123456789"},
                "depot_id": None,
                "vehicle_id": None,
                "address_line1": "879 South New Lane",
                "address_line2": "Flat 2",
                "city": "Wrexham",
                "postcode": "SW1A 1AA",
                "capacities": ["VAN"],
                "driver_type": "INTERNAL",
                "country": "United Kingdom",
                "state": "Wales",
                "account_status": "ACTIVE",
                "live_status": "OFFLINE",
                "profile_photo_url": "https://imagedelivery.net/.../public?expiry=...",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "version": 2,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
)

REACTIVATE_DRIVER["openapi_extra"] = {
    "requestBody": {
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "nullable": True, "maxLength": 2000},
                    },
                },
                "example": {"reason": "Re-enabled after document renewal"},
            }
        }
    }
}

PASSWORD_RESET_DRIVER = create_doc_entry(
    "Admin change driver password",
    {
        200: success_entry(
            "Password changed",
            message="Driver password changed successfully. Driver must log in with the new password.",
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Password does not meet strength requirements",
        ),
    },
)

PASSWORD_RESET_DRIVER_REQUEST_BODY: dict[str, Any] = {
    "openapi_extra": {
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "new_password": {
                                "type": "string",
                                "description": "New password that satisfies global password policy",
                            }
                        },
                        "required": ["new_password"],
                    },
                    "example": {
                        "new_password": "Str0ngP@ssw0rd123",
                    },
                }
            }
        }
    }
}

RESEND_DRIVER_CREDENTIALS = create_doc_entry(
    "Admin: resend driver activation link (email)",
    {
        200: success_entry(
            "Activation link resent",
            message="Activation link resent.",
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Driver must be in PENDING_ACTIVATION state",
        ),
    },
    description=(
        "Resend the driver activation email (deep link to set password in the driver app) for a driver "
        "that is currently pending activation. Invalidates previously unused activation tokens for that user."
    ),
)

PROFILE_PHOTO_UPDATE = create_doc_entry(
    "Upload or update driver profile photo",
    {
        200: success_entry(
            "Profile photo updated",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "profile_photo_key": "cf-image-id-123",
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Unsupported image type or too large"),
    },
)

PROFILE_PHOTO_DELETE = create_doc_entry(
    "Delete driver profile photo",
    {
        200: success_entry(
            "Profile photo removed",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "user_id": "...",
                "driver_code": "DR-001",
                "user": {
                    "id": "...",
                    "email": "driver@example.com",
                    "first_name": "Jane",
                    "last_name": "Driver",
                    "phone": "07123456789",
                },
                "depot_id": None,
                "vehicle_id": None,
                "address_line1": "879 South New Lane",
                "address_line2": "Flat 2",
                "capacities": ["VAN"],
                "driver_type": "INTERNAL",
                "country": "United Kingdom",
                "state": "Wales",
                "city": "Wrexham",
                "postcode": "SW1A 1AA",
                "account_status": "ACTIVE",
                "live_status": "OFFLINE",
                "profile_photo_url": None,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "version": 2,
            },
            message="Profile photo removed",
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
    description=(
        "Admin endpoint. Removes the driver's profile photo from Cloudflare Images (best effort) "
        "and clears ``drivers.profile_photo_key``. Idempotent: succeeds when no photo is stored. "
        "Requires ``Resource.DRIVERS`` WRITE. To upload or replace a photo, use "
        "``PATCH /v1/drivers/{driver_id}/form`` with multipart field ``profile_photo``."
    ),
)

SELF_GET_PROFILE = create_doc_entry(
    "Get authenticated driver's own profile",
    {
        200: success_entry(
            "Driver profile",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "user_id": "00000000-0000-0000-0000-000000000001",
                "driver_code": "DR-001",
                "first_name": "Jane",
                "last_name": "Driver",
                "email": "jane.driver@example.com",
                "phone": "07123456789",
                "profile_photo_url": "https://imagedelivery.net/.../public?expiry=...",
                "requires_password_change": False,
                "terms_accepted_at": "2026-04-09T10:20:30Z",
                "location_consent_at": "2026-04-09T10:20:30Z",
                "map_preference": "GOOGLE_MAPS",
                "version": 3,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver profile not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
    description=(
        "DRIVER self-service endpoint. Returns the authenticated driver's own profile only; "
        "no driver_id is accepted. Requires Authorization Bearer token and X-Client-Type=DRIVER. "
        "The response includes ``email`` (read-only for this route; changing email is not supported here)."
    ),
)

SELF_UPDATE_PROFILE = create_doc_entry(
    "Update authenticated driver's own profile",
    {
        200: success_entry(
            "Driver profile updated",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "user_id": "00000000-0000-0000-0000-000000000001",
                "driver_code": "DR-001",
                "first_name": "Jane",
                "last_name": "Driver",
                "email": "jane.driver@example.com",
                "phone": "07123456789",
                "profile_photo_url": None,
                "requires_password_change": False,
                "terms_accepted_at": "2026-04-09T10:20:30Z",
                "location_consent_at": "2026-04-09T10:20:30Z",
                "map_preference": "GOOGLE_MAPS",
                "version": 4,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver profile not found", code="NOT_FOUND", message="driver with id '...' not found"),
        409: error_entry("Version conflict", code="CONFLICT", message="Resource version does not match expected_version"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Invalid body: unknown fields (e.g. email), blank names, invalid phone, or no field to update",
        ),
    },
    description=(
        "DRIVER self-service endpoint. Partially updates the authenticated driver's identity fields "
        "(``first_name``, ``last_name``, ``phone``) on the linked user. **Email must not appear in the request body** "
        "(extra properties are rejected). At least one of first_name, last_name, or phone is required. "
        "Names cannot be blank; phone must match the allowed international format; "
        "``expected_version`` may be sent for optimistic locking against the driver ``version``."
    ),
)

SELF_GET_ONBOARDING_STATUS = create_doc_entry(
    "Get authenticated driver's onboarding consent status",
    {
        200: success_entry(
            "Onboarding status",
            data={
                "terms_accepted": True,
                "requires_terms_reacceptance": False,
                "location_consent_given": True,
                "terms_accepted_at": "2026-04-09T10:20:30Z",
                "location_consent_at": "2026-04-09T10:20:30Z",
                "map_preference": "GOOGLE_MAPS",
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver profile not found", code="NOT_FOUND", message="driver with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message=(
                "Invalid ``device_installation_id`` / ``X-Device-Installation-Id``: when non-empty after trim, "
                "length must be 8–128 characters (or omit both for hash-only behaviour)."
            ),
        ),
    },
    description=(
        "DRIVER self-service endpoint. Returns the same payload shape as "
        "``DriverSelfOnboardingStatusResponse`` (wrapped in the standard ``{ success, data }`` envelope): "
        "terms and location consent flags, ``requires_terms_reacceptance``, timestamps, and ``map_preference``. "
        "**``requires_terms_reacceptance``** is true when the app should run the terms/consent flow again: "
        "(1) active terms content no longer matches the hash stored on the driver profile, or "
        "(2) an effective per-install id was supplied (see parameters) and the profile already has terms accepted "
        "but there is no audit row for this install id plus the **current** active terms content hash "
        "(e.g. same account, new device). "
        "**Parameters:** optional ``device_installation_id`` query string and/or ``X-Device-Installation-Id`` header — "
        "opaque value the mobile app stores once per install (e.g. UUID in secure storage). "
        "If both ``device_installation_id`` (query) and ``X-Device-Installation-Id`` (header) are non-empty after trim, "
        "the header value is the effective id; otherwise the query value is used when the header is omitted or blank. "
        "Whitespace-only values are treated as omitted. "
        "When no effective id is sent, behaviour is hash-only (legacy clients). "
        "See also ``POST …/onboarding-consents`` — send the same effective id on GET and POST so the returned "
        "``requires_terms_reacceptance`` matches immediately after consent."
    ),
)

SELF_GET_CURRENT_TERMS = create_doc_entry(
    "Get current active terms and conditions for driver onboarding",
    {
        200: success_entry(
            "Current terms and conditions",
            data={
                "id": "00000000-0000-0000-0000-000000000123",
                "title": "SW Couriers Driver Terms and Conditions",
                "clauses": [
                    {
                        "clause_order": 1,
                        "heading": "Acceptance of Terms",
                        "body": "By accessing and using this application...",
                    },
                    {
                        "clause_order": 2,
                        "heading": "Use of Service",
                        "body": "You agree to use the app only for lawful purposes...",
                    }
                ],
                "effective_from": "2026-04-01T00:00:00Z",
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry(
            "No active terms configured",
            code="NOT_FOUND",
            message="driver_terms_and_conditions with id 'active' not found",
        ),
    },
    description=(
        "DRIVER self-service endpoint. Returns the currently active terms and conditions record "
        "that should be displayed before consent submission."
    ),
)

SELF_ACCEPT_ONBOARDING_CONSENTS = create_doc_entry(
    "Accept terms and location consent for authenticated driver",
    {
        200: success_entry(
            "Onboarding consents recorded",
            data={
                "terms_accepted": True,
                "requires_terms_reacceptance": False,
                "location_consent_given": True,
                "terms_accepted_at": "2026-04-09T10:20:30Z",
                "location_consent_at": "2026-04-09T10:20:30Z",
                "map_preference": None,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver profile not found", code="NOT_FOUND", message="driver with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message=(
                "accept_terms_and_conditions and allow_location_access must be true; "
                "optional ``device_installation_id`` (body) or ``X-Device-Installation-Id`` (header) must be "
                "8–128 characters when provided (JSON body wins over header when both are non-empty after trim); "
                "or no active terms configured."
            ),
        ),
    },
    description=(
        "DRIVER self-service endpoint. Records acceptance of terms/agreements and location consent in one operation. "
        "Both ``accept_terms_and_conditions`` and ``allow_location_access`` must be true. "
        "Persists acceptance timestamps on the driver profile and appends an immutable **driver_terms_acceptance_records** "
        "row (audit) including: client IP (from trusted forwarded headers when enabled), ``User-Agent``, "
        "``X-Client-Type``, optional ``device_platform`` / ``device_model`` / ``app_version`` from the JSON body, "
        "and optional per-install id via JSON ``device_installation_id`` or ``X-Device-Installation-Id`` header "
        "(effective id = JSON value if non-empty after trim, otherwise header — same merge as documented on this route’s "
        "OpenAPI parameters). "
        "The JSON response is the same **onboarding status** shape as ``GET …/onboarding-status``, computed using that "
        "effective id so ``requires_terms_reacceptance`` reflects the post-consent state in one round-trip. "
        "Structured log: ``driver_terms_acceptance_recorded``."
    ),
)

SELF_SET_MAP_PREFERENCE = create_doc_entry(
    "Set authenticated driver's preferred map app",
    {
        200: success_entry(
            "Map preference saved",
            data={
                "terms_accepted": True,
                "requires_terms_reacceptance": False,
                "location_consent_given": True,
                "terms_accepted_at": "2026-04-09T10:20:30Z",
                "location_consent_at": "2026-04-09T10:20:30Z",
                "map_preference": "WAZE",
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver profile not found", code="NOT_FOUND", message="driver with id '...' not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Invalid map_preference"),
    },
    description=(
        "DRIVER self-service endpoint. Sets ``map_preference`` to one of: GOOGLE_MAPS, WAZE, APPLE_MAPS. "
        "Returns the same **onboarding status** envelope as ``GET …/onboarding-status`` and ``POST …/onboarding-consents`` "
        "(``DriverSelfOnboardingStatusResponse``). This route does not accept a device id; "
        "``requires_terms_reacceptance`` is therefore computed without per-install context (hash mismatch vs profile only)."
    ),
)

SELF_UPLOAD_PROFILE_PHOTO = create_doc_entry(
    "Upload or replace authenticated driver's profile photo",
    {
        200: success_entry(
            "Profile photo updated",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "user_id": "00000000-0000-0000-0000-000000000001",
                "driver_code": "DR-001",
                "first_name": "Jane",
                "last_name": "Driver",
                "email": "jane.driver@example.com",
                "phone": "07123456789",
                "profile_photo_url": "https://imagedelivery.net/.../public?expiry=...",
                "requires_password_change": False,
                "terms_accepted_at": "2026-04-09T10:20:30Z",
                "location_consent_at": "2026-04-09T10:20:30Z",
                "map_preference": "GOOGLE_MAPS",
                "version": 5,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver profile not found", code="NOT_FOUND", message="driver with id '...' not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Unsupported image type or too large"),
    },
    description=(
        "DRIVER self-service endpoint. Multipart/form-data upload for profile photo. "
        "Field name: photo. Supported types: image/jpeg, image/png. Max size: 5MB. "
        "On success, response includes profile_photo_url as a signed URL."
    ),
)

SELF_DELETE_PROFILE_PHOTO = create_doc_entry(
    "Remove authenticated driver's profile photo",
    {
        200: success_entry(
            "Profile photo removed",
            data={},
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver profile not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
    description=(
        "DRIVER self-service endpoint. Removes the authenticated driver's profile photo. "
        "This operation is idempotent: deleting when no photo exists still returns success. "
        "Success response ``data`` is an empty object (not a full profile payload)."
    ),
)

SELF_HOME_SUMMARY = create_doc_entry(
    "Get mobile home summary for authenticated driver",
    {
        200: success_entry(
            "Home KPI summary",
            data={
                "addresses_attended": 21,
                "addresses_change_pct": 2.4,
                "average_speed_mph": 58.0,
                "average_speed_change_pct": -2.4,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
    },
    description=(
        "DRIVER self-service endpoint for Home tab KPIs. "
        "Supports period presets via query param: today, yesterday, this_week, last_week, last_month, "
        "or an explicit inclusive ``start_date`` / ``end_date`` range (end may be today). "
        "When neither is provided, defaults to ``today``. "
        "Returns only the required KPI cards: addresses_attended and average_speed_mph, "
        "plus percentage gain/drop versus the previous window of equal length."
    ),
)

SELF_ROUTES_LIST = create_doc_entry(
    "List authenticated driver's route history",
    {
        200: success_entry(
            "Paginated route history",
            data={
                "table": {
                    "items": [
                        {
                            "date": "2026-04-08",
                            "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                            "route_code": "RT-214",
                            "vehicle_reg": "SW-21-DR",
                            "type": "DELIVERY",
                            "operational_summary": "18 Stops - 95.0 mins Drive Time",
                            "speeding_count": 2,
                            "harsh_braking_count": 1,
                        },
                        {
                            "date": "2026-04-07",
                            "route_id": "f643f149-cce0-4870-9cf8-77cd8a56d3a4",
                            "route_code": "RT-213",
                            "vehicle_reg": "SW-21-DR",
                            "type": "DELIVERY",
                            "operational_summary": "14 Stops - 82.0 mins Drive Time",
                            "speeding_count": 0,
                            "harsh_braking_count": 0,
                        },
                    ],
                    "total": 2,
                    "page": 1,
                    "size": 20,
                }
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
    },
    description=(
        "DRIVER self-service: paginated **route history** (broader than the board). "
        "Query params: page, size, type (multi-select), search, sort_by, sort_desc. "
        "For the **Upcoming / Past** board use ``GET /me/routes/board`` (tab, search, type, sort)."
    ),
)

SELF_ROUTES_BOARD = create_doc_entry(
    "List routes for All Routes tabs (Upcoming / Past)",
    {
        200: success_entry(
            "Paginated board rows",
            data={
                "table": {
                    "items": [
                        {
                            "route_id": "00000000-0000-0000-0000-000000000001",
                            "route_code": "RT-763",
                            "route_type": "DELIVERY",
                            "service_date": "2026-05-26",
                            "vehicle_reg": "AT42827",
                            "status": "ACTIVE",
                            "total_stops": 16,
                            "estimated_drive_time_minutes": 410.0,
                            "actual_drive_time_minutes": None,
                            "average_route_speed_mph": 28.5,
                            "is_service_date_today": True,
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "size": 20,
                }
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="tab must be upcoming or past",
        ),
    },
    description=(
        "DRIVER self-service: structured list for the **All Routes** screen with **Upcoming** vs **Past** tabs.\n\n"
        "**tab=upcoming** — routes in ``ASSIGNED`` or ``ACTIVE`` (not yet completed).\n"
        "**tab=past** — ``COMPLETED`` routes only.\n\n"
        "Each row includes plan ``service_date``, vehicle registration, counts, planned/actual drive times, "
        "average speed (mph) when distance and actual time exist, and ``is_service_date_today`` "
        "(driver depot-local calendar day vs plan date — same rule as ``GET /me/routes/today``).\n\n"
        "**Query filters (match mobile search / filter bar):**\n"
        "- **search** — substring, case-insensitive on ``route_code`` or vehicle ``registration_number``.\n"
        "- **type** — repeat query key or multi-value: ``PICKUP``, ``DELIVERY`` (omit for all).\n"
        "- **sort** — ``newest_first`` or ``oldest_first`` by plan ``service_date``. "
        "Defaults: **upcoming** = ``oldest_first`` (soonest service day first); **past** = ``newest_first``. "
        "For **upcoming**, ``ACTIVE`` still sorts before ``ASSIGNED`` when the service date is the same.\n\n"
        "Use ``GET /me/routes/{route_id}/summary`` for map/stop detail."
    ),
)

SELF_ROUTE_TODAY = create_doc_entry(
    "Get today's open route for authenticated driver with KPIs",
    {
        200: success_entry(
            "Today's route or explicit null",
            data={
                "current_route": {
                    "route_id": "00000000-0000-0000-0000-000000000000",
                    "route_code": "RT-510",
                    "status": "ASSIGNED",
                    "route_type": "DELIVERY",
                    "service_date": "2026-04-24",
                    "vehicle_reg": "AB12 CDE",
                    "estimated_drive_time_minutes": 95.0,
                    "actual_drive_time_minutes": None,
                    "progress": {"completed_stops": 0, "total_stops": 18, "percent": 0},
                    "todays_deliveries_count": 18,
                    "todays_deliveries_change_pct": 5.9,
                    "estimated_drive_time_change_pct": 7.3,
                    "next_stop": {
                        "stop_id": "11111111-1111-1111-1111-111111111111",
                        "sequence": 1,
                        "stop_type": "DELIVERY",
                        "stop_flow_type": "DELIVERY",
                        "location_name": "Park Ave Store",
                        "tracking_id": "SWBHM-984523",
                        "scheduled_at": "2026-04-24T10:00:00Z",
                    },
                }
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
    },
    description=(
        "DRIVER self-service: open route (ASSIGNED or ACTIVE) whose **plan service_date** matches the target day. "
        "Default target day: **local calendar today in the driver's depot timezone** (`Depot.timezone`; "
        "if the driver has no depot, `Europe/London`). Optional `service_date` overrides that. "
        "`RoutePlan.service_date` is stored as a depot-local calendar day — planners must use the same rule. "
        "If multiple open routes match the same day (e.g. two depots), ACTIVE is preferred over ASSIGNED, then most "
        "recently updated. `current_route` is null when there is no match. Use `GET /me/routes/assigned` for the "
        "ASSIGNED queue across dates."
    ),
)

SELF_ROUTES_ASSIGNED = create_doc_entry(
    "List all ASSIGNED routes for authenticated driver",
    {
        200: success_entry(
            "Paginated ASSIGNED routes",
            data={
                "table": {
                    "items": [
                        {
                            "route_id": "00000000-0000-0000-0000-000000000001",
                            "route_code": "RT-501",
                            "service_date": "2026-04-25",
                            "route_type": "DELIVERY",
                            "vehicle_reg": "AB12 CDE",
                            "total_stops": 12,
                            "status": "ASSIGNED",
                        },
                        {
                            "route_id": "00000000-0000-0000-0000-000000000002",
                            "route_code": "RT-502",
                            "service_date": "2026-04-26",
                            "route_type": "DELIVERY",
                            "vehicle_reg": "AB12 CDE",
                            "total_stops": 8,
                            "status": "ASSIGNED",
                        },
                    ],
                    "total": 2,
                    "page": 1,
                    "size": 20,
                }
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
    },
    description=(
        "DRIVER self-service: all routes in **ASSIGNED** status for this driver (any plan `service_date`, "
        "each stored as a depot-local calendar day per that plan's depot). "
        "Excludes ACTIVE and terminal statuses. Ordered by `service_date` ascending, then `route_code`. "
        "Use `GET /me/routes/today` for the single home-card payload for a given calendar day."
    ),
)

SELF_AVERAGE_SPEED_REPORT = create_doc_entry(
    "List average speed report rows for authenticated driver",
    {
        200: success_entry(
            "Paginated average speed report",
            data={
                "table": {
                    "items": [
                        {
                            "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                            "route_code": "RT-214",
                            "service_date": "2026-04-24",
                            "average_speed_mph": 42.0,
                            "speed_range_min_mph": 38.0,
                            "speed_range_max_mph": 47.0,
                            "severity": "MILD",
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "size": 20,
                }
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="end_date must be on or after start_date"),
    },
    description=(
        "Date-range average speed report for mobile analytics cards. Filters routes by plan `service_date`. "
        "Pass either `period` (today, yesterday, this_week, last_week, last_month) or both `start_date` and "
        "`end_date` (inclusive). `last_month` is the previous calendar month (e.g. on 2026-05-21 → 2026-04-01 to "
        "2026-04-30). Returns route-level average speed, speed range from `LOCATION_PING` telemetry, and "
        "severity derived from speeding over-limit values."
    ),
)

SELF_REPORTS_ABOVE_70_MPH = create_doc_entry(
    "List above-70 MPH speeding incidents across routes (date range)",
    {
        200: success_entry(
            "Paginated speeding incidents",
            data={
                "table": {
                    "items": [
                        {
                            "id": "2b2ac3f9-fdc6-4f27-99a2-6f97e8d10a6f",
                            "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                            "driver_id": "1e9ac9c0-22de-4d46-a4a6-bbbdcbd2dbf2",
                            "route_code": "RT-214",
                            "event_type": "SPEEDING",
                            "occurred_at": "2026-04-08T09:45:00Z",
                            "speed_mph": 74.0,
                            "limit_mph": 70.0,
                            "speed_over_mph": 4.0,
                            "lat": 51.5074,
                            "lng": -0.1278,
                            "metadata": {"speed_mph": 74, "limit_mph": 70},
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "size": 20,
                }
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="end_date must be on or after start_date"),
    },
    description=(
        "Same incident payload as ``GET /me/routes/{route_id}/reports/above-70-mph``, but aggregated for **all** "
        "routes assigned to the driver whose plan ``service_date`` falls in ``start_date``–``end_date`` (inclusive). "
        "Uses DB-side filtering on ``SPEEDING`` events with ``speed_mph`` in event metadata above the telemetry "
        "threshold (70 MPH). Ordered by ``occurred_at`` descending."
    ),
)

SELF_REPORTS_SHARP_BRAKES = create_doc_entry(
    "List harsh braking incidents across routes (date range)",
    {
        200: success_entry(
            "Paginated harsh braking incidents",
            data={
                "table": {
                    "items": [
                        {
                            "id": "fa2ac3f9-fdc6-4f27-99a2-6f97e8d10a6f",
                            "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                            "driver_id": "1e9ac9c0-22de-4d46-a4a6-bbbdcbd2dbf2",
                            "route_code": "RT-214",
                            "event_type": "HARSH_BRAKING",
                            "occurred_at": "2026-04-08T10:05:00Z",
                            "start_speed_mph": 48.0,
                            "end_speed_mph": 11.0,
                            "severity": "HIGH",
                            "lat": 51.5079,
                            "lng": -0.1284,
                            "metadata": {"start_speed_mph": 48, "end_speed_mph": 11},
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "size": 20,
                }
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="end_date must be on or after start_date"),
    },
    description=(
        "Same payload as ``GET /me/routes/{route_id}/reports/sharp-brakes`` for **all** routes in the "
        "``service_date`` window. Ordered by ``occurred_at`` descending."
    ),
)

SELF_ROUTE_SUMMARY = create_doc_entry(
    "Get route summary for authenticated driver",
    {
        200: success_entry("Route summary", data={"route_id": "00000000-0000-0000-0000-000000000000", "route_code": "RT-001", "status": "ACTIVE", "stops": 10, "progress": {"completed_stops": 2, "total_stops": 10, "percent": 20}, "stops_list": [], "map_points": []}),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description="DRIVER self-service endpoint for route details.",
)

SELF_ROUTE_TELEMATICS = create_doc_entry(
    "List route telematics events for authenticated driver",
    {
        200: success_entry(
            "Telematics events",
            data={
                "table": {
                    "items": [
                        {
                            "id": "2b2ac3f9-fdc6-4f27-99a2-6f97e8d10a6f",
                            "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                            "driver_id": "1e9ac9c0-22de-4d46-a4a6-bbbdcbd2dbf2",
                            "route_code": "RT-214",
                            "event_type": "SPEEDING",
                            "occurred_at": "2026-04-08T09:45:00Z",
                            "location_text": "Rosewood Drive, Marlow, UK",
                            "distance_miles": 1.2,
                            "speed_mph": 45.0,
                            "limit_mph": 35.0,
                            "speed_over_mph": 10.0,
                            "start_speed_mph": None,
                            "end_speed_mph": None,
                            "severity": "HIGH",
                            "lat": 51.5074,
                            "lng": -0.1278,
                            "metadata": {"speed_mph": 45, "limit_mph": 35},
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "size": 20,
                }
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description="Supports event_type (multi-select) + page/size for fast list rendering.",
)

SELF_ABOVE_70_MPH_REPORT = create_doc_entry(
    "List above 70 MPH speeding events for selected route",
    {
        200: success_entry(
            "Above 70 MPH report",
            data={
                "table": {
                    "items": [
                        {
                            "id": "2b2ac3f9-fdc6-4f27-99a2-6f97e8d10a6f",
                            "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                            "driver_id": "1e9ac9c0-22de-4d46-a4a6-bbbdcbd2dbf2",
                            "route_code": "RT-214",
                            "event_type": "SPEEDING",
                            "occurred_at": "2026-04-08T09:45:00Z",
                            "speed_mph": 74.0,
                            "limit_mph": 40.0,
                            "speed_over_mph": 34.0,
                            "lat": 51.5074,
                            "lng": -0.1278,
                            "metadata": {"speed_mph": 74, "limit_mph": 40},
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "size": 20,
                }
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description=(
        "Route-scoped safety report: speed violations above 70 MPH for **one** route. "
        "For analytics across many routes in a calendar window, use ``GET /me/reports/above-70-mph`` "
        "with ``start_date`` / ``end_date``. "
        "Supports `page` and `size`. Events can be ingested directly or auto-derived from telemetry."
    ),
)

SELF_SHARP_BRAKE_REPORT = create_doc_entry(
    "List sharp brake events for selected route",
    {
        200: success_entry(
            "Sharp brake report",
            data={
                "table": {
                    "items": [
                        {
                            "id": "fa2ac3f9-fdc6-4f27-99a2-6f97e8d10a6f",
                            "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                            "driver_id": "1e9ac9c0-22de-4d46-a4a6-bbbdcbd2dbf2",
                            "route_code": "RT-214",
                            "event_type": "HARSH_BRAKING",
                            "occurred_at": "2026-04-08T10:05:00Z",
                            "start_speed_mph": 48.0,
                            "end_speed_mph": 11.0,
                            "severity": "HIGH",
                            "lat": 51.5079,
                            "lng": -0.1284,
                            "metadata": {"start_speed_mph": 48, "end_speed_mph": 11, "severity": "HIGH"},
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "size": 20,
                }
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description=(
        "Route-scoped harsh braking report for **one** route. "
        "For a date-range list across routes, use ``GET /me/reports/sharp-brakes``. "
        "Supports `page` and `size`. Events can be ingested directly or derived from telemetry deltas."
    ),
)

SELF_AVERAGE_ROUTE_SPEED = create_doc_entry(
    "Get average route speed for selected route",
    {
        200: success_entry(
            "Average route speed",
            data={
                "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                "route_code": "RT-214",
                "total_distance_km": 22.4,
                "actual_drive_time_min": 54.0,
                "average_speed_mph": 15.5,
                "location_points_count": 18,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description=(
        "Standalone route-level average speed payload. "
        "Average speed is derived from route distance (`total_distance_km`) and `actual_drive_time_min`."
    ),
)

SELF_ACTIVE_DRIVING_MAP = create_doc_entry(
    "Get active driving map payload for selected route",
    {
        200: success_entry(
            "Active driving map",
            data={
                "location": {
                    "start_lat": 52.45,
                    "start_long": -1.89,
                    "end_lat": 52.46,
                    "end_long": -1.90,
                },
                "vehicle": {
                    "latitude": 52.46,
                    "longitude": -1.90,
                    "recorded_at": "2026-04-24T09:15:00+00:00",
                },
                "navigation": {
                    "encoded_polyline": "_p~iF~ps|U_ulLnnqC",
                    "meta": {"polyline_format": "google_encoded", "computed_at": "2026-04-24T08:00:00+00:00"},
                },
                "data": [
                    {
                        "stop_id": "00000000-0000-0000-0000-000000000001",
                        "sequence": 1,
                        "stop_flow_type": "DELIVERY",
                        "tracking_id": "TRK-00000001",
                        "location": "Westside Auto Parts",
                        "longitude": 12.2343434,
                        "latitude": 12.2343434,
                        "packages_count": 2,
                        "status": "COMPLETED",
                    }
                ],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description=(
        "Drive mode map payload. **``data``** — ordered stops with ``stop_id``, ``sequence``, ``stop_flow_type``, "
        "coordinates from ``delivery_stops``, and normalized ``status``. **``vehicle``** — latest "
        "``LOCATION_PING`` with lat/lng (indexed). **``location``** — legacy bbox: earliest vs latest ping on the route. "
        "**``navigation``** — cached polyline on ``routes`` (filled by planner/directions worker); "
        "if stop order changes, ``encoded_polyline`` may be omitted and ``meta.polyline_stale`` set. "
        "Polylines belong per **route** row, not ``route_plans``. They are populated by the planning "
        "pipeline or an async job (directions provider) after route build or stop reorder — not by this GET."
    ),
)

SELF_ROUTE_STOPS = create_doc_entry(
    "List stops for authenticated driver's route",
    {
        200: success_entry(
            "Route stops",
            data={
                "items": [
                    {
                        "stop_id": "72ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                        "sequence": 1,
                        "tracking_id": "TRK-582341",
                        "name": "North Hub",
                        "tracking_summary": "#TRK-582341",
                        "postal_code": "SW1A 2AA",
                        "status": "COMPLETED",
                        "stop_flow_type": "DELIVERY",
                        "estimated_delivery_time": "2026-04-08T10:00:00Z",
                        "actual_delivery_time": "2026-04-08T10:10:00Z",
                        "packages_count": 2,
                    }
                ]
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description=(
        "Used by the All Stops screen for a selected route. "
        "Each item includes ``stop_flow_type`` (``PICKUP``, ``DELIVERY``, ``RETURN``) for the operational leg at that stop."
    ),
)

SELF_STOP_PACKAGES = create_doc_entry(
    "List package IDs for a stop on authenticated driver's route",
    {
        200: success_entry(
            "Stop packages",
            data={
                "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                "stop_id": "72ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "tracking_id": "TRK-582341",
                "items": [
                    {"package_id": "a44a6af0-6eb6-4f3f-986d-e47f3e36de58", "status": "OUT_FOR_DELIVERY"},
                    {"package_id": "b44a6af0-6eb6-4f3f-986d-e47f3e36de58", "status": "OUT_FOR_DELIVERY"},
                ],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route/stop not found", code="NOT_FOUND", message="route_stop with id '...' not found"),
    },
    description="Used by Packages bottom sheet/modal for a stop.",
)

SELF_DELIVERY_DETAIL = create_doc_entry(
    "Get combined delivery detail payload for selected stop",
    {
        200: success_entry(
            "Delivery detail",
            data={
                "location": "North Hub",
                "trackingId": "SWI-984523",
                "postalCode": "SW1A 2AA",
                "status": "IN-PROGRESS",
                "estimatedDeliveryTime": "10:00 am",
                "actualDeliveryTime": None,
                "packagesCount": 2,
                "show_admin_note": True,
                "show_customer_note": True,
                "show_package_issue_stop_notes": True,
                "show_signature_required": True,
                "show_safe_place_allowed": True,
                "admin_note": {
                    "text": "Deliver before 11 AM. Call dispatch if delayed.",
                },
                "customer_note": {
                    "text": "Leave package at back entrance. Ring the bell twice.",
                },
                "package_issue_stop_notes": [
                    {
                        "message": "Outer carton crushed — see photos on file.",
                        "package_ids": [
                            "44444444-4444-4444-4444-444444444441",
                            "44444444-4444-4444-4444-444444444442",
                        ],
                        "images": [
                            {
                                "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                                "image_key": "cf-images/damage-note-1",
                                "sort_order": 1,
                                "image_url": "https://imagedelivery.net/example/damage-note-1/public",
                            }
                        ],
                    }
                ],
                "package_issue": {
                    "hasIssue": True,
                    "description": "Package arrived damaged, box was torn and some items were broken.",
                    "thumbnail_image": "image.png",
                    "images": ["image1.png", "image2.png"],
                },
                "packages_summary": {
                    "totalPackages": 2,
                    "totalWeight": "18 kg",
                },
                "package_breakdown": [
                    {
                        "package_id": "pkg-1",
                        "size": "40 x 30 x 25 cm",
                        "weight": "8 kg",
                    },
                    {
                        "package_id": "pkg-2",
                        "size": "50 x 40 x 35 cm",
                        "weight": "10 kg",
                    },
                ],
                "signature_required": {
                    "required": True,
                    "message": "Customer must sign upon delivery.",
                },
                "safe_place_allowed": {
                    "required": False,
                    "message": "This package must be handed over in person.",
                },
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route/stop not found", code="NOT_FOUND", message="route_stop with id '...' not found"),
    },
    description=(
        "Single combined endpoint for Delivery Detail screen. "
        "Aggregates packages, admin/customer note summaries, structured `PACKAGE_ISSUE_NOTE` rows (`package_issue_stop_notes` "
        "with `package_ids` and optional `images` signed URLs), legacy missing/damaged `package_issue`, and signatures. "
        "`package_issue.description` is driven by the latest missing report for the stop when present. "
        "`status` is one of: `COMPLETED`, `IN-PROGRESS`, `PENDING`."
    ),
)

SELF_IMPORTANT_DELIVERY_NOTE = create_doc_entry(
    "Get important delivery notes for selected stop",
    {
        200: success_entry(
            "Important delivery note",
            data={
                "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                "stop_id": "72ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "items": [],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route/stop not found", code="NOT_FOUND", message="route_stop with id '...' not found"),
    },
    description=(
        "Dedicated endpoint for important notes only. "
        "Includes blocking notes and note types: IMPORTANT, URGENT, CRITICAL."
    ),
)

SELF_STOP_NOTES = create_doc_entry(
    "List structured notes for selected stop",
    {
        200: success_entry(
            "Stop notes",
            data={
                "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                "stop_id": "72ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "delivery_stop_id": "98ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "notes_hash": "7f3a08309f06845b1765ddf4109fd540f0cad8d4e7f9f0eb4504bbf95f4cf421",
                "requires_acknowledgement": True,
                "acknowledged": False,
                "acknowledged_at": None,
                "items": [
                    {
                        "id": "a44a6af0-6eb6-4f3f-986d-e47f3e36de58",
                        "note_type": "ADMIN",
                        "message": "Deliver before 11 AM. Call dispatch if delayed.",
                        "is_blocking": True,
                        "sort_order": 1,
                        "package_ids": [],
                        "images": [],
                    },
                    {
                        "id": "b44a6af0-6eb6-4f3f-986d-e47f3e36de59",
                        "note_type": "PACKAGE_ISSUE_NOTE",
                        "message": "Parcel received with damaged outer packaging.",
                        "is_blocking": False,
                        "sort_order": 2,
                        "package_ids": [
                            "44444444-4444-4444-4444-444444444441",
                            "44444444-4444-4444-4444-444444444442",
                        ],
                        "images": [],
                    },
                ],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route/stop not found", code="NOT_FOUND", message="route_stop with id '...' not found"),
    },
    description=(
        "Driver-only stop execution endpoint. "
        "Returns notes plus computed `notes_hash` and acknowledgement status. "
        "Each item includes `package_ids` (sorted UUIDs) for `PACKAGE_ISSUE_NOTE`; other types return an empty list. "
        "The hash includes `package_ids`, so changing linked packages requires re-acknowledgement when blocking notes apply. "
        "Use this response to decide whether note acknowledgement is required before scan/status/complete."
    ),
)

# Stop execution endpoint docs are consumed by the consolidated
# `app/modules/drivers/v1/stop_execution_routes.py` router.
SELF_STOP_NOTES_ACK = create_doc_entry(
    "Acknowledge stop notes",
    {
        200: success_entry(
            "Notes acknowledged",
            data={
                "acknowledged": True,
                "acknowledged_at": "2026-04-09T10:20:30Z",
                "notes_hash": "7f3a08309f06845b1765ddf4109fd540f0cad8d4e7f9f0eb4504bbf95f4cf421",
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Notes changed; refresh notes and acknowledge latest version"),
    },
    description=(
        "Acknowledges the current stop notes snapshot (`notes_hash`). "
        "If notes change, hash changes and the driver must acknowledge again."
    ),
)

SELF_STOP_PACKAGE_SCAN = create_doc_entry(
    "Scan package reference / UUID for selected stop",
    {
        200: success_entry(
            "Package matched",
            data={
                "package_id": "a44a6af0-6eb6-4f3f-986d-e47f3e36de58",
                "reference_number": "PKG-00001001",
                "status": "OUT_FOR_DELIVERY",
                "matched_by": "PACKAGE",
                "master_label_id": None,
                "packages_confirmed": 1,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Package not found / wrong stop / already finalized at this stop",
        ),
    },
    description=(
        "Resolves a scan for the selected stop. "
        "``matched_by`` is ``PACKAGE`` when the value matched a parcel reference or UUID. "
        "For **PICKUP** stops, scanning the order **master label** (same value as ``orders.master_label_id``) "
        "confirms all parcels still in pre-pickup statuses in one step (``matched_by`` = ``MASTER_LABEL``, "
        "``packages_confirmed`` > 0). "
        "Rejects non-stop packages, wrong-stop parcels, and scans when the package is **already finalized** "
        "for the current stop leg (e.g. return terminal already recorded)."
    ),
)

SELF_STOP_PACKAGE_STATUS = create_doc_entry(
    "Finalize package status for selected stop",
    {
        200: success_entry(
            "Package finalized (delivery example)",
            data={
                "package_id": "a44a6af0-6eb6-4f3f-986d-e47f3e36de58",
                "status": "DELIVERED_TO_CUSTOMER",
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="NOTES_ACK_REQUIRED / status not allowed for this stop_flow_type (PICKUP blocked) / outcome already recorded",
        ),
    },
    description=(
        "Sets terminal package status for the selected stop. "
        "Request body: required `status`, optional `notes`. "
        "Blocking stop notes must be acknowledged first when required.\n\n"
        "Allowed `status` values depend on ``route_stops.stop_flow_type`` (do **not** mix delivery vs return dispositions):\n\n"
        "* **DELIVERY** (Select Delivery Status) — ``DELIVERED_TO_CUSTOMER``, ``LEFT_AT_SAFE_PLACE``, ``CUSTOMER_NOT_HOME``, ``REFUSED_BY_CUSTOMER``. "
        "Do **not** send ``SENDER_NOT_HOME`` or ``RETURNED_TO_SENDER`` on delivery.\n"
        "* **RETURN** (Select Return Status) — ``RETURNED_TO_SENDER`` (stored as ``RETURNED``), "
        "``SENDER_NOT_HOME`` (stored as ``CUSTOMER_NOT_HOME``), or ``DISPOSED``. "
        "Legacy request strings ``RETURNED`` / ``CUSTOMER_NOT_HOME`` on return are **rejected**.\n"
        "* **PICKUP** — do **not** use this endpoint. Scan parcels or the order **master label** to move packages to "
        "``LOADED_FOR_DELIVERY``, then **complete** the stop (no disposition picker).\n\n"
        "On **RETURN**, once a terminal is recorded it **cannot** be changed to a different one "
        "(re-sending the **same** status is allowed)."
    ),
)

SELF_STOP_PACKAGES_BATCH_STATUS = create_doc_entry(
    "Batch finalize return package statuses for selected stop",
    {
        200: success_entry(
            "Return outcomes applied",
            data={
                "updated_count": 2,
                "items": [
                    {"package_id": "a44a6af0-6eb6-4f3f-986d-e47f3e36de58", "status": "RETURNED"},
                    {"package_id": "b44a6af0-6eb6-4f3f-986d-e47f3e36de59", "status": "RETURNED"},
                ],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Return stops only / invalid package_ids / outcome already recorded / NOTES_ACK_REQUIRED",
        ),
    },
    description=(
        "**Return stops only.** Applies one return disposition to many ``packages.id`` values in a single transaction. "
        "Use after scanning: send the package UUIDs from scan responses together with ``status`` "
        "(``RETURNED_TO_SENDER``, ``SENDER_NOT_HOME``, or ``DISPOSED``). Same rules as "
        "``PATCH …/packages/{package_id}/status`` (blocking notes must be acknowledged first; "
        "cannot change an already-recorded **different** terminal outcome; re-applying the **same** outcome is allowed). "
        "For **delivery** or **pickup** stops, use per-package PATCH or scans plus complete — this endpoint is rejected."
    ),
)

SELF_STOP_PACKAGE_MISSING_REPORT = create_doc_entry(
    "Report missing package for selected stop",
    {
        200: success_entry(
            "Missing package reported",
            data={
                "package_id": "a44a6af0-6eb6-4f3f-986d-e47f3e36de58",
                "status": "MISSING",
                "reason_code": "NOT_IN_MY_VEHICLE",
                "report_id": "e44a6af0-6eb6-4f3f-986d-e47f3e36de58",
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="This package does not belong to the selected stop"),
    },
    description=(
        "Reports package as missing and finalizes package to `MISSING` in one action."
    ),
)

SELF_STOP_PENDING_PACKAGES = create_doc_entry(
    "List pending packages for selected stop",
    {
        200: success_entry(
            "Pending packages",
            data={
                "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                "stop_id": "72ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "delivery_stop_id": "98ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "items": [
                    {
                        "package_id": "a44a6af0-6eb6-4f3f-986d-e47f3e36de58",
                        "reference_number": "PKG-00001001",
                        "status": "OUT_FOR_DELIVERY",
                    }
                ],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route/stop not found", code="NOT_FOUND", message="route_stop with id '...' not found"),
    },
    description=(
        "Returns packages for the selected stop that are not finalized yet. "
        "Use this endpoint to populate the package dropdown on report-missing flow. "
        "On **RETURN** stops, terminals such as **RETURNED**, **DISPOSED**, and **CUSTOMER_NOT_HOME** are excluded; "
        "on **DELIVERY** stops, standard delivery outcomes are excluded when finalized."
    ),
)

SELF_STOP_PACKAGE_PROGRESS = create_doc_entry(
    "Get package scan progress for selected stop",
    {
        200: success_entry(
            "Package scan progress",
            data={
                "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                "stop_id": "72ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "delivery_stop_id": "98ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "stop_name": "Downtown Book Depot",
                "tracking_id": "TRK-582341",
                "stop_flow_type": "DELIVERY",
                "master_label_id": None,
                "packages_to_scan": 3,
                "scanned_packages": 2,
                "completion_percent": 66,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route/stop not found", code="NOT_FOUND", message="route_stop with id '...' not found"),
    },
    description="Summary card payload for stop package scan progress.",
)

SELF_STOP_POD_UPLOAD_URL = create_doc_entry(
    "Upload POD photo for selected stop",
    {
        200: success_entry(
            "POD photo uploaded",
            data={
                "delivery_stop_id": "98ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "photos_count": 2,
                "items": [
                    {
                        "id": "a44a6af0-6eb6-4f3f-986d-e47f3e36de58",
                        "image_id": "cf-image-id-123",
                        "image_url": "https://imagedelivery.net/.../cf-image-id-123/public?expiry=...",
                        "sort_order": 1,
                    },
                    {
                        "id": "b44a6af0-6eb6-4f3f-986d-e47f3e36de58",
                        "image_id": "cf-image-id-456",
                        "image_url": "https://imagedelivery.net/.../cf-image-id-456/public?expiry=...",
                        "sort_order": 2,
                    },
                ],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Maximum 5 photos can be uploaded per request",
        ),
    },
    description=(
        "Multipart/form-data endpoint (`files`) that uploads one or more photos to Cloudflare Images and persists "
        "POD photo rows for the selected stop in a single call. Supports max 5 photos per request and max 5 total "
        "photos per stop."
    ),
)

SELF_STOP_POD_PHOTOS = create_doc_entry(
    "List POD photos for selected stop",
    {
        200: success_entry(
            "POD photos",
            data={
                "delivery_stop_id": "98ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "photos_count": 2,
                "items": [
                    {
                        "id": "a44a6af0-6eb6-4f3f-986d-e47f3e36de58",
                        "image_id": "cf-image-id-123",
                        "image_url": "https://imagedelivery.net/.../cf-image-id-123/public?expiry=...",
                        "sort_order": 1,
                    },
                    {
                        "id": "b44a6af0-6eb6-4f3f-986d-e47f3e36de58",
                        "image_id": "cf-image-id-456",
                        "image_url": "https://imagedelivery.net/.../cf-image-id-456/public?expiry=...",
                        "sort_order": 2,
                    },
                ],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route/stop not found", code="NOT_FOUND", message="route_stop with id '...' not found"),
    },
    description="Returns current POD photos already attached to the selected stop.",
)

SELF_STOP_POD_CONFIRM = create_doc_entry(
    "Confirm POD photo by image ID",
    {
        200: success_entry("POD photo confirmed", data={"delivery_stop_id": "98ff31ff-f45e-49ab-81ca-a0f8f1547af5", "photos_count": 2}),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Maximum of 5 POD photos is allowed"),
    },
    description=(
        "Legacy compatibility endpoint. Confirms an existing Cloudflare image id (`image_key`) against the stop; "
        "new clients should use POST `/pod/photos/upload` which already uploads and persists in one step."
    ),
)

SELF_STOP_POD_DELETE = create_doc_entry(
    "Delete POD photo for selected stop",
    {
        200: success_entry("POD photo deleted", data={"delivery_stop_id": "98ff31ff-f45e-49ab-81ca-a0f8f1547af5", "photos_count": 1}),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Photo not found", code="NOT_FOUND", message="stop_pod_photo with id '...' not found"),
    },
    description="Deletes a previously confirmed POD photo from the selected stop.",
)

SELF_STOP_SIGNATURE = create_doc_entry(
    "Save customer signature for selected stop",
    {
        200: success_entry(
            "Signature saved",
            data={
                "delivery_stop_id": "98ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "signature_image_key": "drivers/123/pod/stop-abc/signature.png",
                "signature_required": True,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
    },
    description="Stores customer signature image key for the selected stop.",
)

SELF_STOP_READINESS = create_doc_entry(
    "Get completion readiness for selected stop",
    {
        200: success_entry(
            "Readiness status",
            data={
                "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                "stop_id": "72ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "delivery_stop_id": "98ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "stop_flow_type": "DELIVERY",
                "master_label_id": None,
                "return_requires_pod": False,
                "notes_ok": True,
                "packages_ok": True,
                "pod_ok": True,
                "signature_ok": True,
                "pending_package_ids": [],
                "photo_count": 2,
                "signature_required": False,
                "notes_hash": "7f3a08309f06845b1765ddf4109fd540f0cad8d4e7f9f0eb4504bbf95f4cf421",
                "acknowledged": True,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
    },
    description=(
        "Returns machine-readable completion gates: notes, package terminal states, POD, and signature requirements. "
        "``stop_flow_type`` and optional ``master_label_id`` describe the leg (PICKUP / DELIVERY / RETURN). "
        "**PICKUP** stops skip POD and signature gates (``pod_ok`` and ``signature_ok`` always true). "
        "**DELIVERY** requires POD (1–5 photos) and signature when ``signature_required`` applies. "
        "**RETURN** skips signature. On RETURN, ``return_requires_pod`` is true when any package is **RETURNED** "
        "(returned-to-sender); then ``pod_ok`` follows the same 1–5 photo rule as delivery. "
        "Sender-not-home (**CUSTOMER_NOT_HOME**), **DISPOSED**, etc. do not require stop-level POD."
        "\n\nSplit checks: ``GET …/readiness/notes``, ``…/packages``, ``…/pod``, ``…/signature`` return the same "
        "gates with context fields only for that check."
    ),
)

SELF_STOP_READINESS_NOTES = create_doc_entry(
    "Readiness: blocking stop notes",
    {
        200: success_entry(
            "Notes gate",
            data={
                "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                "stop_id": "72ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "delivery_stop_id": "98ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "ok": True,
                "requires_acknowledgement": False,
                "acknowledged": True,
                "notes_hash": "7f3a08309f06845b1765ddf4109fd540f0cad8d4e7f9f0eb4504bbf95f4cf421",
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
    },
    description="Same ``notes_ok`` rule as aggregate ``GET …/readiness``; includes ``notes_hash`` for acknowledgement flows.",
)

SELF_STOP_READINESS_PACKAGES = create_doc_entry(
    "Readiness: package terminalization",
    {
        200: success_entry(
            "Packages gate",
            data={
                "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                "stop_id": "72ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "delivery_stop_id": "98ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "stop_flow_type": "DELIVERY",
                "ok": False,
                "pending_package_ids": ["a44a6af0-6eb6-4f3f-986d-e47f3e36de58"],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
    },
    description="Same ``packages_ok`` / ``pending_package_ids`` as aggregate readiness for this stop leg.",
)

SELF_STOP_READINESS_POD = create_doc_entry(
    "Readiness: proof of delivery photos",
    {
        200: success_entry(
            "POD gate",
            data={
                "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                "stop_id": "72ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "delivery_stop_id": "98ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "stop_flow_type": "DELIVERY",
                "ok": True,
                "photo_count": 2,
                "return_requires_pod": False,
                "stop_pod_required": True,
                "min_photos_when_required": 1,
                "max_photos_allowed": 5,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
    },
    description=(
        "Same ``pod_ok`` as aggregate readiness. ``stop_pod_required`` is true for DELIVERY and for RETURN when "
        "``return_requires_pod`` (returned-to-sender). PICKUP always has ``stop_pod_required`` false and ``ok`` true."
    ),
)

SELF_STOP_READINESS_SIGNATURE = create_doc_entry(
    "Readiness: customer signature",
    {
        200: success_entry(
            "Signature gate",
            data={
                "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                "stop_id": "72ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "delivery_stop_id": "98ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "stop_flow_type": "DELIVERY",
                "ok": True,
                "signature_required": False,
                "captured": False,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
    },
    description=(
        "Same ``signature_ok`` as aggregate readiness. **RETURN** and **PICKUP** always return ``ok`` true; "
        "``captured`` indicates whether a signature image key is stored on the POD row."
    ),
)

SELF_STOP_COMPLETE_DELIVERY = create_doc_entry(
    "Complete selected stop after package finalization and POD checks",
    {
        200: success_entry(
            "Stop completed",
            data={
                "stop_id": "72ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                "status": "COMPLETED",
                "message": "Stop marked as completed",
                "readiness": {
                    "route_id": "8f76a81b-cc5e-4f3f-9a34-3bcb1ddf9901",
                    "stop_id": "72ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                    "delivery_stop_id": "98ff31ff-f45e-49ab-81ca-a0f8f1547af5",
                    "stop_flow_type": "DELIVERY",
                    "master_label_id": None,
                    "return_requires_pod": False,
                    "notes_ok": True,
                    "packages_ok": True,
                    "pod_ok": True,
                    "signature_ok": True,
                    "pending_package_ids": [],
                    "photo_count": 2,
                    "signature_required": False,
                    "notes_hash": "7f3a08309f06845b1765ddf4109fd540f0cad8d4e7f9f0eb4504bbf95f4cf421",
                    "acknowledged": True,
                },
            },
        ),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="STOP_NOT_READY / POD_INCOMPLETE / SIGNATURE_REQUIRED"),
    },
    description=(
        "Atomically completes a stop only when readiness gates pass (`notes_ok`, `packages_ok`, `pod_ok`, `signature_ok`). "
        "PICKUP skips POD/signature. RETURN skips signature; POD is required only when **RETURNED** is recorded (see GET …/readiness, ``return_requires_pod``)."
    ),
)

SELF_ROUTE_ACTION = create_doc_entry(
    "Route action for authenticated driver (start/pause/resume/finish)",
    {
        200: success_entry("Route state updated", data={"route_id": "00000000-0000-0000-0000-000000000000", "status": "ACTIVE", "message": "Route started"}),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
)

SELF_STOP_ACTION = create_doc_entry(
    "Stop action for authenticated driver (arrive/complete/fail)",
    {
        200: success_entry("Stop updated", data={"stop_id": "00000000-0000-0000-0000-000000000000", "status": "ARRIVED", "message": "Stop marked as arrived"}),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route stop not found", code="NOT_FOUND", message="route_stop with id '...' not found"),
    },
)

SELF_TELEMETRY_BATCH = create_doc_entry(
    "Ingest telemetry points for authenticated driver",
    {
        200: success_entry("Telemetry accepted", data={"accepted": 25}),
        401: error_401_entry(),
        403: error_entry("Only DRIVER users can access this endpoint", code="FORBIDDEN", message="Not allowed"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="route_id is required for each telemetry point"),
    },
    description=(
        "Stores each telemetry point as `LOCATION_PING`. "
        "Backend also derives safety events (`SPEEDING`, `HARSH_BRAKING`) from incoming speed data."
    ),
)

SCHEDULE_GET = create_doc_entry(
    "Get weekly driver schedule",
    {
        200: success_entry(
            "Weekly work schedule",
            data={
                "days": [
                    {"day_of_week": 0, "is_active": True, "start_time": "08:00:00", "end_time": "17:00:00"},
                ],
                "total_weekly_hours": 40.0,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
    },
)

SCHEDULE_UPDATE = create_doc_entry(
    "Replace weekly driver schedule",
    {
        200: success_entry(
            "Weekly work schedule updated",
            data={
                "days": [
                    {"day_of_week": 0, "is_active": True, "start_time": "08:00:00", "end_time": "17:00:00"},
                ],
                "total_weekly_hours": 40.0,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Daily or weekly hours exceed configured limits",
        ),
    },
    description=(
        "Requires Resource.DRIVERS WRITE. Replaces the recurring weekly working pattern for a driver. "
        "The backend enforces a maximum daily hours limit and a maximum weekly hours limit; "
        "if the requested pattern would exceed either, the API responds with HTTP 422.\n\n"
        "Example JSON body:\n"
        "{\n"
        '  "days": [\n'
        '    {"day_of_week": 0, "is_active": true, "start_time": "08:00:00", "end_time": "17:00:00"},\n'
        '    {"day_of_week": 1, "is_active": true, "start_time": "08:00:00", "end_time": "17:00:00"}\n'
        "  ]\n"
        "}"
    ),
)

SCHEDULE_UPDATE_DAY = create_doc_entry(
    "Update single day in weekly schedule",
    {
        200: success_entry(
            "Weekly work schedule updated",
            data={
                "days": [
                    {"day_of_week": 0, "is_active": True, "start_time": "08:00:00", "end_time": "17:00:00"},
                ],
                "total_weekly_hours": 40.0,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Daily or weekly hours exceed configured limits",
        ),
    },
    description=(
        "Requires Resource.DRIVERS WRITE. Updates a single day in the recurring weekly working pattern for a driver. "
        "The backend enforces a maximum daily hours limit and a maximum weekly hours limit; "
        "if the requested pattern would exceed either, the API responds with HTTP 422.\n\n"
        "Example JSON body:\n"
        '{ "day_of_week": 0, "is_active": true, "start_time": "08:00:00", "end_time": "17:00:00" }'
    ),
)

SHIFTS_LIST = create_doc_entry(
    "List driver shifts",
    {
        200: success_entry(
            "List of shifts",
            data={
                "items": [
                    {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "driver_id": "00000000-0000-0000-0000-000000000001",
                        "date": "2024-01-01",
                        "start_time": "08:00:00",
                        "end_time": "16:00:00",
                        "status": "PLANNED",
                    }
                ]
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
    },
)

SHIFTS_MUTATE = create_doc_entry(
    "Create driver shift",
    {
        200: success_entry(
            "Shift updated",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "date": "2024-01-01",
                "start_time": "08:00:00",
                "end_time": "16:00:00",
                "status": "PLANNED",
            },
        ),
        201: success_entry(
            "Shift created",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "date": "2024-01-01",
                "start_time": "08:00:00",
                "end_time": "16:00:00",
                "status": "PLANNED",
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        409: error_entry("Shift conflict", code="CONFLICT", message="Shift overlaps with an existing shift for this driver"),
    },
)

SHIFT_GET_FULL = create_doc_entry(
    "Get driver shift by ID",
    {
        200: success_entry(
            "Shift detail",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "date": "2024-01-01",
                "start_time": "08:00:00",
                "end_time": "16:00:00",
                "status": "PLANNED",
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Shift not found", code="NOT_FOUND", message="shift with id '...' not found"),
    },
)

SHIFT_UPDATE = create_doc_entry(
    "Update driver shift",
    {
        200: success_entry(
            "Shift updated",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "date": "2024-01-01",
                "start_time": "08:00:00",
                "end_time": "16:00:00",
                "status": "PLANNED",
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Shift not found", code="NOT_FOUND", message="shift with id '...' not found"),
        409: error_entry("Shift conflict", code="CONFLICT", message="Shift overlaps with an existing shift for this driver"),
    },
)

SHIFT_DELETE = create_doc_entry(
    "Delete driver shift",
    {
        200: success_entry(
            "Shift deleted",
            data={},
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Shift not found", code="NOT_FOUND", message="shift with id '...' not found"),
    },
)

TRAFFIC_VIOLATIONS_LIST = create_doc_entry(
    "List driver traffic violations",
    {
        200: success_entry(
            "Traffic violations list",
            data={
                "items": [
                    {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "driver_id": "00000000-0000-0000-0000-000000000001",
                        "occurred_at": "2024-01-01T14:30:00Z",
                        "violation_type": "SPEEDING",
                        "amount": "45.00",
                        "status": "PAID",
                        "notes": "Exceeded speed limit by 7mph",
                        "proofs": [
                            {
                                "id": "00000000-0000-0000-0000-000000000099",
                                "url": "drivers/driver-id/traffic-violations/file-key",
                                "content_type": "application/pdf",
                                "size_bytes": 12345,
                                "created_at": "2024-01-01T14:31:00Z",
                            }
                        ],
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 50,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
    description=(
        "Requires `Resource.DRIVERS` READ. Proof `url` fields are presigned downloads when generation succeeds. "
        "Does **not** require driver document OTP or `X-Driver-Doc-Access-Token`."
    ),
)


LIST_DRIVER_ROUTE_HISTORY = create_doc_entry(
    "List driver route history",
    {
        200: success_entry(
            "Paginated list of historical routes for a driver",
            data={
                "table": {
                    "items": [
                        {
                            "date": "2024-01-01",
                            "route_id": "00000000-0000-0000-0000-000000000000",
                            "route_code": "RT-001",
                            "vehicle_reg": "AB12CDE",
                            "type": "DELIVERY",
                            "operational_summary": "15 Stops - 85.5 mins Drive Time",
                            "speeding_count": 2,
                            "harsh_braking_count": 1,
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "size": 50,
                    "pages": 1,
                },
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission (DRIVERS read required)", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
    description=(
        "Requires Resource.DRIVERS READ. "
        "Returns a paginated history of routes for a given driver, including human-friendly route codes, "
        "vehicle registration (resolved via route.vehicle_id), route type, a simple operational summary, and "
        "aggregated telematics counts for SPEEDING and HARSH_BRAKING. "
        "Supports query params: page, size, search, sort_by=date, sort_desc, and type as repeated list keys. "
        "Date is derived from route.created_at."
    ),
)


DRIVER_SCHEDULE_AVAILABILITY_CALENDAR = create_doc_entry(
    "Get driver schedule & availability calendar",
    {
        200: success_entry(
            "Calendar entries aggregated from shifts, time off, holidays, and routes",
            data={
                "from_date": "2026-04-01",
                "to_date": "2026-04-30",
                "summary": {
                    "shifts_count": 4,
                    "time_off_count": 2,
                    "holidays_count": 3,
                    "routes_count": 2,
                },
                "events": [
                    {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "source": "SHIFT",
                        "title": "06:00 - 14:00",
                        "start_at": "2026-04-10T06:00:00Z",
                        "end_at": "2026-04-10T14:00:00Z",
                        "is_all_day": False,
                        "status": "CONFIRMED",
                        "shift_status": "CONFIRMED",
                    },
                    {
                        "id": "00000000-0000-0000-0000-000000000002",
                        "source": "TIME_OFF",
                        "title": "SICK_LEAVE",
                        "start_at": "2026-04-11T00:00:00Z",
                        "end_at": "2026-04-11T23:59:59Z",
                        "is_all_day": True,
                        "time_off_type": "SICK_LEAVE",
                        "is_paid": False,
                    },
                    {
                        "id": "00000000-0000-0000-0000-000000000003",
                        "source": "HOLIDAY",
                        "title": "Spring Bank",
                        "start_at": "2026-04-12T00:00:00Z",
                        "end_at": "2026-04-12T23:59:59Z",
                        "is_all_day": True,
                        "holiday_name": "Spring Bank",
                    },
                ],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission (DRIVERS read required)", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
        422: error_entry("Invalid request", code="VALIDATION_ERROR", message="from_date cannot be after to_date"),
    },
    description=(
        "Requires Resource.DRIVERS READ. "
        "Returns calendar entries in a date range for one driver by aggregating shifts, time off, "
        "applicable holidays, and routes. "
        "Supports repeated-list filters via query params: "
        "event_source, shift_status, time_off_type, route_type, route_status "
        "(e.g. event_source=SHIFT&event_source=TIME_OFF)."
    ),
)


GET_ROUTE_SUMMARY = create_doc_entry(
    "Get route summary (stops and progress)",
    {
        200: success_entry(
            "Route summary payload",
            data={
                "route_id": "00000000-0000-0000-0000-000000000000",
                "route_code": "RT-001",
                "date": "2024-01-01",
                "status": "COMPLETED",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "vehicle_reg": "AB12CDE",
                "stops": 15,
                "estimated_drive_time_minutes": 90.0,
                "actual_drive_time_minutes": 85.5,
                "progress": {
                    "completed_stops": 15,
                    "total_stops": 15,
                    "percent": 100,
                },
                "stops_list": [
                    {
                        "sequence": 1,
                        "status": "COMPLETED",
                        "stop_flow_type": "DELIVERY",
                        "label": "BS1 5TY \u2013 Park Ave Store",
                        "tracking_id": "TRK-739204",
                        "lat": 51.4545,
                        "lng": -2.5879,
                        "estimated_arrival": "2024-01-01T09:00:00Z",
                        "actual_arrival": "2024-01-01T09:05:00Z",
                    }
                ],
                "map_points": [],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission (DRIVERS read required)", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description=(
        "Requires Resource.DRIVERS READ. "
        "Returns a single route summary including route code, basic status fields, stop count, "
        "estimated and actual drive time minutes, overall completion progress, and a flattened list of stops. "
        "Vehicle registration is resolved from route.vehicle_id. Date is derived from route.created_at."
    ),
)


LIST_ROUTE_TELEMATICS = create_doc_entry(
    "List route telematics / safety events",
    {
        200: success_entry(
            "Paginated list of telematics events for a route",
            data={
                "table": {
                    "items": [
                        {
                            "id": "00000000-0000-0000-0000-000000000999",
                            "route_id": "00000000-0000-0000-0000-000000000000",
                            "driver_id": "00000000-0000-0000-0000-000000000001",
                            "route_code": "SWBHM-212111",
                            "event_type": "SPEEDING",
                            "occurred_at": "2024-01-01T09:15:00Z",
                            "location_text": "Rosewood Drive, Marlow, UK",
                            "distance_miles": 1.2,
                            "speed_mph": 82,
                            "limit_mph": 65,
                            "speed_over_mph": 17,
                            "start_speed_mph": 65,
                            "end_speed_mph": 50,
                            "severity": "MEDIUM",
                            "lat": 51.5074,
                            "lng": -0.1278,
                            "metadata": {
                                "speed_mph": 82,
                                "limit_mph": 65,
                                "speed_over_mph": 17,
                                "route_code": "SWBHM-212111",
                                "location_text": "Rosewood Drive, Marlow, UK",
                                "distance_miles": 1.2,
                                "severity": "MEDIUM",
                                "start_speed_mph": 65,
                                "end_speed_mph": 50,
                            },
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "size": 50,
                    "pages": 1,
                },
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission (DRIVERS read required)", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Route not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description=(
        "Requires Resource.DRIVERS READ. "
        "Lists telematics/safety events (e.g. SPEEDING, HARSH_BRAKING) for a single route, "
        "with optional filtering by event_type as repeated list keys and standard page/size pagination. "
        "Event payload includes occurred_at, coordinates, and promoted UI fields (route_code, location_text, distance_miles, "
        "speed_mph, limit_mph, speed_over_mph, start_speed_mph, end_speed_mph, severity). "
        "These values are sourced from route_events.event_metadata (JSONB) and also preserved under metadata."
    ),
)

TRAFFIC_VIOLATIONS_MUTATE = create_doc_entry(
    "Create driver traffic violation",
    {
        201: success_entry(
            "Traffic violation created",
            data={
                "violation": {
                    "id": "00000000-0000-0000-0000-000000000000",
                    "driver_id": "00000000-0000-0000-0000-000000000001",
                    "occurred_at": "2024-01-01T14:30:00Z",
                    "violation_type": "SPEEDING",
                    "amount": "45.00",
                    "status": "UNPAID",
                    "notes": "Driver exceeded speed limit",
                    "proofs": [
                        {
                            "id": "00000000-0000-0000-0000-000000000099",
                            "url": "drivers/driver-id/traffic-violations/file-key",
                            "content_type": "image/jpeg",
                            "size_bytes": 12345,
                            "created_at": "2024-01-01T14:31:00Z",
                        }
                    ],
                },
                "proof_results": [
                    {
                        "index": 0,
                        "filename": "ticket.jpg",
                        "status": "success",
                        "error": None,
                        "proof": {
                            "id": "00000000-0000-0000-0000-000000000099",
                            "url": "drivers/driver-id/traffic-violations/file-key",
                            "content_type": "image/jpeg",
                            "size_bytes": 12345,
                            "created_at": "2024-01-01T14:31:00Z",
                        },
                    },
                    {
                        "index": 1,
                        "filename": "ticket.exe",
                        "status": "failed",
                        "error": "Invalid content type (allowed: image/jpeg, image/png, application/pdf, image/heic)",
                        "proof": None,
                    },
                ],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver/violation not found", code="NOT_FOUND", message="Resource not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Invalid data, status must be PAID or UNPAID, or one of the proof files is invalid",
        ),
    },
    description=(
        "Multipart/form-data endpoint to create a traffic violation. "
        "Supports uploading zero or more proof files via the `proofs` field (repeatable). "
        "Proof files support JPG, PNG, PDF and HEIC up to 25MB each."
    ),
)

TRAFFIC_VIOLATION_UPDATE = create_doc_entry(
    "Update driver traffic violation",
    {
        200: success_entry(
            "Traffic violation updated",
            data={
                "violation": {
                    "id": "00000000-0000-0000-0000-000000000000",
                    "driver_id": "00000000-0000-0000-0000-000000000001",
                    "occurred_at": "2024-01-01T14:30:00Z",
                    "violation_type": "SPEEDING",
                    "amount": "45.00",
                    "status": "PAID",
                    "notes": "Updated notes",
                    "proofs": [],
                },
                "proof_results": [],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver/violation not found", code="NOT_FOUND", message="Resource not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Invalid data; status must be PAID or UNPAID, or one of the proof files is invalid",
        ),
    },
    description=(
        "Multipart/form-data endpoint to update a traffic violation. "
        "Any of the create fields may be supplied (violation_type, amount, date+time, status, notes). "
        "Supports uploading zero or more new proof files via the repeatable `proofs` field (JPG, PNG, PDF, HEIC up to 25MB each). "
        "To delete a proof, use DELETE /v1/drivers/traffic-violations/proofs/{proof_id}.\n\n"
        "Example multipart/form-data fields (body):\n"
        "- status=PAID\n"
        "- notes=Fine paid on 2024-02-01\n"
        "- amount=45.00\n"
        "- date=2024-01-01\n"
        "- time=14:30:00\n"
        "- proofs=<file1>\n"
        "- proofs=<file2>\n"
    ),
)

TRAFFIC_VIOLATION_GET_FULL = create_doc_entry(
    "Get driver traffic violation by ID",
    {
        200: success_entry(
            "Traffic violation detail",
            data={
                "id": "00000000-0000-0000-0000-000000000000",
                "driver_id": "00000000-0000-0000-0000-000000000001",
                "occurred_at": "2024-01-01T14:30:00Z",
                "violation_type": "SPEEDING",
                "amount": "45.00",
                "status": "PAID",
                "notes": "Driver exceeded speed limit",
                "proofs": [
                    {
                        "id": "00000000-0000-0000-0000-000000000099",
                        "url": "drivers/driver-id/traffic-violations/file-key",
                        "content_type": "application/pdf",
                        "size_bytes": 12345,
                        "created_at": "2024-01-01T14:31:00Z",
                    }
                ],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver/violation not found", code="NOT_FOUND", message="Resource not found"),
    },
)

TRAFFIC_VIOLATION_DELETE = create_doc_entry(
    "Delete driver traffic violation",
    {
        200: success_entry(
            "Traffic violation deleted",
            data={},
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver/violation not found", code="NOT_FOUND", message="Resource not found"),
    },
)

TRAFFIC_VIOLATION_ADD_PROOFS = create_doc_entry(
    "Add proof files to a traffic violation",
    {
        201: success_entry(
            "Proof files added",
            data={
                "violation": {
                    "id": "00000000-0000-0000-0000-000000000000",
                    "driver_id": "00000000-0000-0000-0000-000000000001",
                    "occurred_at": "2024-01-01T14:30:00Z",
                    "violation_type": "SPEEDING",
                    "amount": "45.00",
                    "status": "PAID",
                    "notes": "Updated notes",
                    "proofs": [
                        {
                            "id": "00000000-0000-0000-0000-000000000099",
                            "url": "drivers/driver-id/traffic-violations/file-key",
                            "content_type": "image/jpeg",
                            "size_bytes": 12345,
                            "created_at": "2024-01-01T14:31:00Z",
                        }
                    ],
                },
                "proof_results": [
                    {
                        "index": 0,
                        "filename": "ticket-2.jpg",
                        "status": "success",
                        "error": None,
                        "proof": {
                            "id": "00000000-0000-0000-0000-000000000099",
                            "url": "drivers/driver-id/traffic-violations/file-key",
                            "content_type": "image/jpeg",
                            "size_bytes": 12345,
                            "created_at": "2024-01-01T14:31:00Z",
                        },
                    }
                ],
            },
            message="Proofs uploaded",
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver/violation not found", code="NOT_FOUND", message="Resource not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Invalid proof file type or size"),
    },
    description=(
        "Multipart/form-data endpoint. Upload one or more files in repeatable field `proofs` (JPG/PNG/PDF/HEIC up to 25MB each). "
        "Response includes `proof_results[]` describing which files succeeded/failed."
    ),
)

TRAFFIC_VIOLATION_DELETE_PROOF = create_doc_entry(
    "Delete traffic violation proof",
    {
        200: success_entry(
            "Proof deleted",
            data={},
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Proof not found", code="NOT_FOUND", message="Resource not found"),
    },
)

GET_DRIVER_ACTIVITY_LOG = create_doc_entry(
    "List driver activity log (admin)",
    {
        200: success_entry(
            "Paginated activity rows for the driver profile table",
            data={
                "items": [
                    {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "timestamp": "2026-02-23T08:12:34Z",
                        "event": "Login",
                        "user_type": "Driver",
                        "activity_performed_by": "driver@example.com",
                        "ip_address": "192.168.1.45",
                    },
                    {
                        "id": "00000000-0000-0000-0000-000000000002",
                        "timestamp": "2026-02-23T09:30:00Z",
                        "event": "Shift assigned",
                        "user_type": "Admin",
                        "activity_performed_by": "ops.manager@swcouriers.co.uk",
                        "ip_address": "192.168.1.10",
                    },
                ],
                "total": 100,
                "page": 1,
                "size": 50,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Driver not found", code="NOT_FOUND", message="driver with id '...' not found"),
    },
    description=(
        "**Activity Log table (list).** Each item includes only fields shown on the driver profile "
        "Activity Log screen: timestamp, event, user type, actor email, IP, plus `id` for the detail call.\n\n"
        "**Scope:** Rows are included when any of the following holds:\n"
        "- The actor is the linked driver user (`user_id` equals the driver’s `user_id`).\n"
        "- `entity_type` is `driver` and `entity_id` equals this driver’s id.\n"
        "- `old_value` or `new_value` JSON contains `driver_id` equal to this driver’s id (e.g. shifts, documents).\n\n"
        "**Auth:** `Resource.DRIVERS` READ only. Activity log is **not** part of the driver compliance document OTP flow "
        "(no `X-Driver-Doc-Access-Token`).\n\n"
        "**Query:** `page`, `size` (default 50, max 100), optional `from_date` / `to_date` (ISO datetimes), "
        "`sort` = `asc` | `desc` (by timestamp), optional `search` across actor email/name, action, reason, IP, audit ref.\n\n"
        "**Detail:** `GET /v1/drivers/{driver_id}/activity-log/{audit_log_id}` for full row + redacted payloads."
    ),
)

GET_DRIVER_ACTIVITY_LOG_DETAIL = create_doc_entry(
    "Get single driver activity log entry",
    {
        200: success_entry(
            "Full audit record (redacted)",
            data={
                "id": "00000000-0000-0000-0000-000000000001",
                "timestamp": "2026-02-23T08:12:34Z",
                "event": "Shift assigned",
                "user_type": "Admin",
                "activity_performed_by": "admin@example.com",
                "ip_address": "192.168.1.10",
                "audit_ref": "AUD-2026-ABCDEF01",
                "action": "driver.shift.create",
                "category": "Fleet",
                "event_type": "SHIFT_CREATED",
                "severity": "NOTICE",
                "entity_type": "driver",
                "entity_id": "00000000-0000-0000-0000-000000000099",
                "entity_ref": None,
                "reason": None,
                "user_id": "00000000-0000-0000-0000-000000000002",
                "user_role": "ADMIN",
                "organization_id": None,
                "user_agent": "Mozilla/5.0 ...",
                "browser": "Google Chrome",
                "device": "Desktop",
                "os": "Windows 11",
                "old_value": None,
                "new_value": {"driver_id": "00000000-0000-0000-0000-000000000099"},
            },
        ),
        401: error_401_entry(),
        403: error_entry("Insufficient permission", code="FORBIDDEN", message="Not allowed"),
        404: error_entry("Not found", code="NOT_FOUND", message="audit_log with id '...' not found"),
    },
    description=(
        "**Activity Log row detail (modal / drawer).** Returns the same display fields as the list row, "
        "plus technical metadata: `audit_ref`, `action`, `category`, `event_type`, `severity`, "
        "`entity_type` / `entity_id` / `entity_ref`, `reason`, actor ids, `user_agent`, parsed browser/device/OS, "
        "and **`old_value` / `new_value`** with server-side redaction (passwords, tokens, card data patterns, etc.).\n\n"
        "**404** if the audit id does not exist or is **not in scope** for this driver (same rules as the list endpoint).\n\n"
        "**Auth:** Same as the list endpoint (`Resource.DRIVERS` READ only; no driver document OTP header)."
    ),
)

# Swagger UI requestBody hints for multipart fields
TRAFFIC_VIOLATIONS_MUTATE["openapi_extra"] = {
    "requestBody": {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "violation_type": {"type": "string"},
                        "amount": {"type": "string"},
                        "date": {"type": "string", "format": "date"},
                        "time": {"type": "string"},
                        "status": {"type": "string"},
                        "notes": {"type": "string", "nullable": True},
                        "proofs": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": "Optional proof files (send 0..N).",
                        },
                    },
                    "required": ["violation_type", "amount", "date", "time", "status"],
                }
            }
        }
    }
}

TRAFFIC_VIOLATION_ADD_PROOFS["openapi_extra"] = {
    "requestBody": {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "proofs": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": "One or more proof files (repeatable field).",
                        }
                    },
                    "required": ["proofs"],
                }
            }
        }
    }
}

TRAFFIC_VIOLATION_UPDATE["openapi_extra"] = {
    "requestBody": {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "violation_type": {"type": "string", "description": "TrafficViolationType enum value"},
                        "amount": {"type": "string", "description": "Decimal amount as string, e.g. 45.00"},
                        "date": {"type": "string", "format": "date"},
                        "time": {"type": "string", "description": "HH:MM:SS"},
                        "status": {"type": "string", "description": "TrafficViolationStatus enum value"},
                        "notes": {"type": "string", "nullable": True},
                        "proofs": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": "Optional proof files (send 0..N).",
                        },
                    },
                }
            }
        }
    },
    "responses": {
        "200": {
            "description": "Traffic violation updated",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "data": {
                            "violation": {
                                "id": "00000000-0000-0000-0000-000000000000",
                                "driver_id": "00000000-0000-0000-0000-000000000001",
                                "occurred_at": "2024-01-01T14:30:00Z",
                                "violation_type": "SPEEDING",
                                "amount": "45.00",
                                "status": "PAID",
                                "notes": "Fine paid",
                                "proofs": [
                                    {
                                        "id": "00000000-0000-0000-0000-000000000099",
                                        "url": "drivers/driver-id/traffic-violations/file-key",
                                        "content_type": "application/pdf",
                                        "size_bytes": 12345,
                                        "created_at": "2024-01-01T14:31:00Z",
                                    }
                                ],
                            },
                            "proof_results": [
                                {
                                    "index": 0,
                                    "filename": "receipt.pdf",
                                    "status": "success",
                                    "error": None,
                                    "proof": {
                                        "id": "00000000-0000-0000-0000-000000000099",
                                        "url": "drivers/driver-id/traffic-violations/file-key",
                                        "content_type": "application/pdf",
                                        "size_bytes": 12345,
                                        "created_at": "2024-01-01T14:31:00Z",
                                    },
                                }
                            ],
                        },
                        "message": "OK",
                    }
                }
            },
        }
    },
}

_WORK_SCHEDULE_DAY_EXAMPLE = {
    "date": "2025-04-22",
    "day_type": "WORKING",
    "shift_hours": "06:00 - 14:00",
    "shift_status": "CONFIRMED",
    "time_off_type": None,
    "time_off_is_paid": None,
    "holiday_name": None,
    "route": {
        "route_id": "00000000-0000-0000-0000-000000000001",
        "route_code": "RT-652",
        "route_status": "COMPLETED",
        "vehicle_registration": "YC2 3LD",
    },
}

SELF_WORK_SCHEDULE_WEEKLY = create_doc_entry(
    "Get weekly work schedule",
    {
        200: success_entry(
            "Weekly work schedule",
            data={
                "start_date": "2025-04-21",
                "end_date": "2025-04-27",
                "days": [_WORK_SCHEDULE_DAY_EXAMPLE],
            },
        ),
        400: error_entry("Invalid date range", code="VALIDATION_ERROR", message="start_date must be before end_date"),
        401: error_401_entry(),
    },
    description=(
        "Returns one entry per calendar day in the requested week range. "
        "Each day's `day_type` is one of: WORKING, TIME_OFF, HOLIDAY, REST. "
        "A `route` object is included whenever a route exists for that service_date. "
        "Use `start_date` + `end_date` to request any 7-day window (max 31 days)."
    ),
)

SELF_WORK_SCHEDULE_MONTHLY = create_doc_entry(
    "Get monthly work schedule",
    {
        200: success_entry(
            "Monthly work schedule",
            data={
                "month": "2025-04",
                "days": [_WORK_SCHEDULE_DAY_EXAMPLE],
            },
        ),
        400: error_entry("Invalid month format", code="VALIDATION_ERROR", message="month must be YYYY-MM"),
        401: error_401_entry(),
    },
    description=(
        "Returns one entry per calendar day for the full requested month. "
        "Pass `month` as `YYYY-MM` (e.g. `2025-04`). "
        "Each day's `day_type` is one of: WORKING, TIME_OFF, HOLIDAY, REST."
    ),
)

SELF_WORK_SCHEDULE_DAY = create_doc_entry(
    "Get single day work schedule detail",
    {
        200: success_entry(
            "Day detail",
            data={
                **_WORK_SCHEDULE_DAY_EXAMPLE,
                "vehicle": "YC2 3LD",
            },
        ),
        401: error_401_entry(),
    },
    description=(
        "Returns full detail for a single calendar day including shift hours, "
        "time-off type, holiday name, vehicle registration, and route info."
    ),
)
