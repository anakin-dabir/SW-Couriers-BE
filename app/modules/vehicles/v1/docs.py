from __future__ import annotations

from app.core.swagger import create_doc_entry, custom_entry, error_401_entry, error_entry, success_entry

LIST_VEHICLES = create_doc_entry(
    "List vehicles",
    {
        200: success_entry(
            "Vehicles for the current page",
            data={
                "items": [
                    {
                        "id": "...",
                        "registration_number": "MK24 XYP",
                        "make": "Ford",
                        "model": "Transit",
                        "year": 2022,
                        "live_status": "IDLE",
                        "availability": "ACTIVE",
                        "tax": {"status": "PAID", "remaining_days": 45, "due_date": "2026-06-01"},
                        "mot": {"status": "VALID", "remaining_days": 120, "due_date": "2026-08-01"},
                        "service": {"status": "VALID", "display_unit": "DAYS", "display_value": 30},
                        "defects": {"total": 2, "pending": 1, "in_progress": 0},
                        "images": ["https://..."],
                    }
                ],
                "total": 100,
                "page": 1,
                "size": 20,
                "pages": 5,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Not allowed", code="FORBIDDEN", message="Insufficient permissions"),
    },
    description=(
        "Browse the fleet in pages. Results can be narrowed by registration prefix, what the vehicle is doing right now, "
        "whether it is active or in maintenance, and MOT or tax state. Each row shows compliance at a glance, "
        "how many defects are open, and time-limited links to its photos."
    ),
)

LIST_DELETED_VEHICLES = create_doc_entry(
    "List deleted vehicles (audit log)",
    {
        200: success_entry(
            "Deleted vehicle snapshots",
            data={
                "items": [
                    {
                        "id": "former-vehicle-uuid",
                        "registration_number": "AB12 CDE",
                        "make": "Ford",
                        "model": "Transit",
                        "vehicle_type": "INTERNAL",
                        "deletion_reason": "Sold",
                        "created_at": "2026-04-01T12:00:00Z",
                        "deleted_by": {
                            "first_name": "Admin",
                            "last_name": "User",
                            "email": "admin@example.com",
                        },
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 20,
                "pages": 1,
            },
        ),
        401: error_401_entry(),
    },
    description=(
        "Returns paginated rows from the vehicle deletion log. Each row is a snapshot taken at delete time "
        "(vehicle id, registration, make, model, internal/external type, reason, who deleted, when). "
        "The main `vehicles` row is removed after deletion; this list is the historical record."
    ),
)

GET_VEHICLE = create_doc_entry(
    "Get vehicle details",
    {
        200: success_entry(
            "Full vehicle profile",
            data={
                "id": "...",
                "registration_number": "MK24 XYP",
                "availability": "ACTIVE",
                "live_status": "IDLE",
                "images": [{"id": "img-uuid-1", "url": "https://..."}, {"id": "img-uuid-2", "url": "https://..."}],
                "next_service_card": {"display_unit": "DAYS", "display_value": 14},
                "current_mileage_card": {"display_unit": "MILES", "display_value": 1200},
                "efficiency_card": {"display_unit": "MPG", "display_value": 32},
            },
        ),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
    },
    description=(
        "Loads everything needed for the vehicle screen: specifications, fleet and live status, preferred driver if set, "
        "each stored photo as id plus signed URL, and summary cards for next service, current mileage, and efficiency."
    ),
)

CREATE_VEHICLE = create_doc_entry(
    "Register a new vehicle",
    {
        201: custom_entry(
            "Vehicle created; any document or photo uploads that failed are listed so you can retry them",
            example={
                "success": True,
                "message": "Vehicle registered — 1 document(s) uploaded, 1 failed",
                "data": {
                    "id": "...",
                    "fleet_number": "VAN-001",
                    "registration_number": "MK24 XYP",
                    "images": [{"id": "img-uuid", "url": "https://..."}],
                    "documents": [{"id": "...", "title": "MOT Certificate", "document_type": "MOT"}],
                },
                "failed_documents": [{"index": 1, "filename": "insurance.pdf", "reason": "File upload failed, please retry this document"}],
                "failed_images": [],
            },
        ),
        401: error_401_entry(),
        409: error_entry("Duplicate registration", code="CONFLICT", message="Vehicle with registration '...' already exists"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="vehicle_data must be valid JSON"),
    },
    description=(
        "Send structured vehicle details together with optional photos (up to two) and optional documents (up to five), "
        "each document paired with matching metadata. `max_continuous_driving_hours` and `break_duration_minutes` are "
        "required in `vehicle_data`. The response returns the new vehicle plus id and signed URL for each stored image. "
        "Other endpoints that attach photos may report upload failures in a slightly different shape."
    ),
)

UPDATE_SPECS = create_doc_entry(
    "Update vehicle specifications",
    {
        200: custom_entry(
            "Specs updated; optional image upload failures listed",
            example={
                "success": True,
                "message": "Specifications updated",
                "data": {"id": "...", "make": "Ford", "model": "Transit", "images": [{"id": "img-uuid", "url": "https://..."}]},
                "failed_images": [],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
        409: error_entry("Concurrent edit", code="CONFLICT", message="Vehicle was modified by another request"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="vehicle_data must be valid JSON"),
    },
    description=(
        "Send as multipart/form-data. `vehicle_data` is a JSON string with the same fields as before: make, model, "
        "fleet_custom_name, year, fuel_type, cargo_volume_m3, max_payload_kg, service_interval_miles, "
        "service_interval_months, average_mpg (non-electric) or range_miles (electric), optional preferred_driver_id, "
        "max_continuous_driving_hours, break_duration_minutes. "
        "When `service_interval_months` changes, `next_service_due` is shifted by re-anchoring the current due date "
        "to the implied last-service date (due minus old interval) then adding the new interval; if no previous due "
        "or months were stored, it is set from today plus the new interval. Changing only `service_interval_miles` does "
        "not change `next_service_due` or `last_service_mileage`. "
        "Optional `images` (up to 2 files) and optional `deleted_image_ids` (JSON array of image UUID strings). "
        "Order: apply spec updates, delete listed images, then append new uploads. "
        "`failed_images` lists any per-file upload errors without rolling back saved specs."
    ),
)

UPDATE_MILEAGE = create_doc_entry(
    "Update recorded mileage",
    {
        200: success_entry("Mileage updated", data={"id": "...", "current_mileage": 43000, "current_mileage_card": {"display_unit": "MILES", "display_value": 0}}),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="New mileage cannot be less than current"),
    },
    description="Sets the odometer reading; it cannot be lower than the value already stored.",
)

CHANGE_AVAILABILITY = create_doc_entry(
    "Change vehicle availability",
    {
        200: success_entry("Availability changed", data={"id": "...", "availability": "UNAVAILABLE", "availability_effective_from": "2026-03-01"}),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
        409: error_entry("Concurrent edit", code="CONFLICT", message="Vehicle was modified by another request"),
    },
    description=("Marks the vehicle as active, unavailable, or in maintenance, with the dates those states apply. " "Returns the updated vehicle."),
)

GET_SCHEDULE = create_doc_entry(
    "Vehicle schedule for a date range",
    {
        200: success_entry(
            "Schedule",
            data={
                "events": [
                    {
                        "date": "2026-04-01",
                        "type": "MAINTENANCE",
                        "details": {
                            "maintenance_id": "...",
                            "maintenance_reference": "MT-01272",
                            "maintenance_types": ["MOT"],
                            "maintenance_description": "Mot",
                        },
                    },
                    {
                        "date": "2026-04-10",
                        "type": "OUT_FOR_PICKUP",
                        "details": {
                            "route_id": "00000000-0000-0000-0000-000000000000",
                            "route_code": "RT-652",
                            "route_type": "PICKUP",
                            "status_label": "Out for Pickup",
                            "driver_name": "Sam Taylor",
                            "stops_count": 18,
                        },
                    },
                ],
                "utilization_summary": {
                    "completed_delivery_days": 5,
                    "completed_delivery_percent": 17,
                    "completed_pickup_days": 2,
                    "completed_pickup_percent": 7,
                    "out_for_delivery_days": 0,
                    "out_for_delivery_percent": 0,
                    "out_for_pickup_days": 1,
                    "out_for_pickup_percent": 3,
                    "maintenance_days": 4,
                    "maintenance_percent": 13,
                    "unavailable_days": 2,
                    "unavailable_percent": 7,
                    "available_days": 16,
                    "available_percent": 53,
                },
            },
        ),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="start_date must be on or before end_date"),
    },
    description=(
        "Calendar-style view: per-day events merge `vehicle_schedule_entries` with live planning routes for the vehicle "
        "(via `routes.vehicle_id` and the plan `service_date`). Types distinguish completed vs in-progress and pickup vs "
        "delivery. Maintenance rows include `maintenance_reference` and a human `maintenance_description`. "
        "`utilization_summary` counts days in the requested range with matching integer `*_percent` (0–100, rounded). "
        "Optional repeated query param `event_types` uses calendar filter kinds only: `DELIVERY_ROUTE` (completed + in-progress "
        "delivery), `PICKUP_ROUTE` (completed + in-progress pickup), `MAINTENANCE`, `UNAVAILABLE`. Omit to return every day. "
        "Each `events[].type` remains a granular `ScheduleEventType`. Utilization always reflects the full range. "
        "Same-day conflicts use priority: UNAVAILABLE > MAINTENANCE > completed route > in-progress route > AVAILABLE. "
        "When route and schedule entry tie, the planning route is returned."
    ),
)

LIST_VEHICLE_ROUTE_HISTORY = create_doc_entry(
    "Vehicle route history",
    {
        200: success_entry(
            "Paginated routes for this vehicle",
            data={
                "table": {
                    "items": [
                        {
                            "date": "2026-02-14",
                            "route_id": "00000000-0000-0000-0000-000000000000",
                            "route_code": "RT-12345",
                            "driver_name": "Sam Taylor",
                            "type": "DELIVERY",
                            "estimated_miles": 47.0,
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "size": 50,
                    "pages": 1,
                }
            },
        ),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
    },
    description=(
        "Requires Resource.VEHICLE_MANAGEMENT WRITE. "
        "Paginated rows for the route history table: date, route identifiers, assigned driver name, route type, and estimated miles "
        "(from ``routes.total_distance_km`` converted to miles when present). "
        "Results are ordered newest-first by route creation time. "
        "Query: ``type`` (repeat for PICKUP/DELIVERY), ``search`` (route_code or assigned driver name), ``page``, ``size``."
    ),
)

GET_VEHICLE_ROUTE_SUMMARY = create_doc_entry(
    "Vehicle route overview (header + telemetry)",
    {
        200: success_entry(
            "Route metadata, progress, and aggregated telematics (no stop rows)",
            data={
                "route_id": "...",
                "route_code": "RT-12345",
                "route_type": "DELIVERY",
                "date": "2026-02-14",
                "status": "COMPLETED",
                "driver_id": "...",
                "driver_name": "Sam Taylor",
                "vehicle_reg": "AB12CDE",
                "estimated_miles": 47.0,
                "stops": 12,
                "estimated_drive_time_minutes": 258,
                "actual_drive_time_minutes": 318,
                "progress": {"completed_stops": 12, "total_stops": 12, "percent": 100},
                "telemetry": {
                    "speeding_events": 3,
                    "harsh_braking_events": 5,
                    "max_speed_mph": 78.0,
                    "average_speed_mph": 46.0,
                },
                "encoded_polyline": None,
            },
        ),
        401: error_401_entry(),
        404: error_entry("Not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description=(
        "Requires Resource.VEHICLE_MANAGEMENT WRITE. "
        "Route-level panel for the vehicle route screen: identifiers, schedule, driver, distances, completion progress, "
        "telemetry aggregates (event counts, max recorded speeding mph, average mph from distance vs actual drive time when available), "
        "and optional planned ``encoded_polyline``. Does not include stops; use ``GET .../routes/{route_id}/stops`` for the table."
    ),
)

LIST_VEHICLE_ROUTE_STOPS = create_doc_entry(
    "Vehicle route stops (paginated)",
    {
        200: success_entry(
            "Paginated route stops",
            data={
                "table": {
                    "items": [
                        {
                            "route_stop_id": "...",
                            "sequence": 1,
                            "stop_flow_type": "DELIVERY",
                            "status": "COMPLETED",
                            "tracking_id": "TRK-00001",
                            "label": "BS1 5TY – Park Ave Store",
                            "estimated_arrival": "2026-02-14T08:00:00Z",
                            "actual_arrival": "2026-02-14T08:20:00Z",
                            "notes_count": 2,
                        }
                    ],
                    "total": 12,
                    "page": 1,
                    "size": 20,
                    "pages": 1,
                }
            },
        ),
        401: error_401_entry(),
        404: error_entry("Not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description=(
        "Requires Resource.VEHICLE_MANAGEMENT WRITE. "
        "Paginated rows for the stops table. Pickup legs use order + pickup address for ``label`` and ``master_label_id`` as ``tracking_id`` when there is no delivery stop."
    ),
)

GET_VEHICLE_ROUTE_STOP_DETAIL = create_doc_entry(
    "Vehicle route stop detail",
    {
        200: success_entry(
            "Stop summary with packages",
            data={
                "route_id": "...",
                "route_stop_id": "...",
                "stop_flow_type": "DELIVERY",
                "sequence": 1,
                "status": "COMPLETED",
                "tracking_id": "TRK-00001",
                "location_label": "BS1 5TY – Park Ave Store",
                "postcode": "BS1 5TY",
                "order_id": "...",
                "delivery_stop_id": "...",
                "scheduled_at": "2026-02-14T08:00:00Z",
                "actual_at": "2026-02-14T08:20:00Z",
                "total_packages": 3,
                "total_weight_kg": 18.0,
                "packages": [],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Not found", code="NOT_FOUND", message="route_stop or route not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="Pickup route stop has no order_id"),
    },
    description=(
        "Requires Resource.VEHICLE_MANAGEMENT WRITE. "
        "For ``stop_flow_type`` PICKUP: resolves ``order`` and ``pickup_addresses`` and lists all packages on that order. "
        "For DELIVERY/RETURN with a ``delivery_stop_id``: resolves ``delivery_stops`` and lists packages assigned to that stop. "
        "Returns 422 when required foreign keys are missing for the stop type."
    ),
)

LIST_VEHICLE_ROUTE_SPEEDING_EVENTS = create_doc_entry(
    "Vehicle route speeding events",
    {
        200: success_entry(
            "All SPEEDING route_events for the route (newest first)",
            data={"items": []},
        ),
        401: error_401_entry(),
        404: error_entry("Not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description=(
        "Requires Resource.VEHICLE_MANAGEMENT WRITE. "
        "Returns every ``SPEEDING`` telemetry row for this route when ``routes.vehicle_id`` matches the vehicle. "
        "Not paginated; use for modals such as the speeding events list."
    ),
)

LIST_VEHICLE_ROUTE_HARSH_BRAKING_EVENTS = create_doc_entry(
    "Vehicle route harsh braking events",
    {
        200: success_entry(
            "All HARSH_BRAKING route_events for the route (newest first)",
            data={"items": []},
        ),
        401: error_401_entry(),
        404: error_entry("Not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description=(
        "Requires Resource.VEHICLE_MANAGEMENT WRITE. "
        "Returns every ``HARSH_BRAKING`` telemetry row for this route when ``routes.vehicle_id`` matches the vehicle. "
        "Not paginated; use for modals such as the harsh braking list."
    ),
)

GET_VEHICLE_ROUTE_NOTES = create_doc_entry(
    "Vehicle route stop notes",
    {
        200: success_entry(
            "All stop notes for each route stop",
            data={
                "route_id": "...",
                "stops": [
                    {
                        "route_stop_id": "...",
                        "sequence": 1,
                        "delivery_stop_id": "...",
                        "notes": [],
                    }
                ],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Not found", code="NOT_FOUND", message="route with id '...' not found"),
    },
    description=(
        "Requires Resource.VEHICLE_MANAGEMENT WRITE. "
        "Returns every operational ``stop_notes`` row (with images and package links) for all delivery stops on this route, "
        "grouped by ``route_stop``. Pickup-only legs without a ``delivery_stop_id`` return an empty ``notes`` list."
    ),
)

DELETE_VEHICLE = create_doc_entry(
    "Delete a vehicle",
    {
        200: success_entry("Vehicle deleted", message="Vehicle removed successfully"),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="reason is required"),
    },
    description=(
        "JSON body: `reason` (required, 1–2000 chars). Inserts a snapshot into the vehicle deletion log, "
        "removes files from storage, then deletes the vehicle row and dependent records. "
        "Use GET /vehicles/deleted to browse historical deletions."
    ),
)

GET_FLEET_STATS = create_doc_entry(
    "Fleet overview statistics",
    {
        200: success_entry(
            "Fleet stats",
            data={"total_vehicles": 24, "active_vehicles": 19, "in_maintenance": 3, "compliance_alerts": 2},
        ),
        401: error_401_entry(),
    },
)

GET_COMPLIANCE = create_doc_entry(
    "Compliance summary from latest MOT/insurance documents plus next service (`ComplianceSummaryResponse`). "
    "Each card includes a percentage bar showing validity used vs remaining.",
    {
        200: success_entry(
            "Compliance summary",
            data={
                "mot": {
                    "status": "VALID",
                    "expiry_date": "2026-05-19",
                    "remaining_days": 70,
                    "reference_number": None,
                    "provider": None,
                    "percentage_bar": {"validity_used": 45, "remaining": 55},
                },
                "tax": {
                    "status": "PAID",
                    "due_date": "2026-06-10",
                    "remaining_days": 22,
                    "percentage_bar": {"validity_used": 20, "remaining": 80},
                },
                "insurance": {
                    "status": "VALID",
                    "expiry_date": "2026-05-19",
                    "remaining_days": 210,
                    "reference_number": "AXA-FLT-984522",
                    "provider": "PrimeSure UK Ltd",
                    "percentage_bar": {"validity_used": 80, "remaining": 20},
                },
                "service_interval": {
                    "status": "VALID",
                    "expiry_date": "2026-05-19",
                    "remaining_days": None,
                    "remaining_miles": 3200,
                    "display_unit": "MILES",
                    "display_value": 3200,
                    "percentage_bar": {"validity_used": 68, "remaining": 32},
                },
            },
        ),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
    },
    description=(
        "Shows whether MOT, road tax, insurance, and next service are in order, with date-based fields and "
        "a `percentage_bar` for each card (`validity_used` and `remaining`). "
        "For `service_interval`, the same 'whichever comes first' rule as the fleet list picks miles vs days (~50 mi ≈ 1 day); "
        "only the chosen axis has `remaining_days` or `remaining_miles` set, and `percentage_bar` matches that axis only. "
        "Scenario A — miles-first, still within interval: e.g. `display_unit` MILES, `remaining_miles` 3200, "
        "`remaining_days` null, `display_value` 3200, status VALID. "
        "Scenario B — miles-first, overdue: e.g. status OVERDUE, `remaining_miles` -1, `remaining_days` null, "
        "`display_value` 0, `percentage_bar` `{validity_used: 100, remaining: 0}`. "
        "Scenario C — days-first, still within interval: e.g. `display_unit` DAYS, `remaining_days` 45, "
        "`remaining_miles` null, `display_value` 45, `expiry_date` matches `next_service_due`, status VALID. "
        "Scenario D — days-first, overdue: e.g. status OVERDUE, `remaining_days` -12, `remaining_miles` null, "
        "`display_value` 0, `percentage_bar` `{validity_used: 100, remaining: 0}`."
    ),
)

LOG_MAINTENANCE = create_doc_entry(
    "Log a maintenance record for a vehicle",
    {
        201: success_entry("Maintenance logged", data={"id": "..."}, message="Maintenance record saved"),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
    },
)

LIST_MAINTENANCE = create_doc_entry(
    "Maintenance history",
    {
        200: success_entry(
            "Maintenance records",
            data={
                "items": [
                    {"reference": "MT-00001", "maintenance_types": ["OIL_CHANGE"], "garage": "Quick Fit Motors"},
                ],
                "total": 0,
                "page": 1,
                "size": 20,
                "pages": 0,
            },
        ),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
    },
    description=(
        "Paginated workshop history; each job has its own reference number. You can filter by one or more job types "
        "(oil change, repair, MOT, tyres, inspection, bodywork); anything that matches at least one chosen type is included. "
        "Optional `search` matches reference (MT- plus digits), garage name, or maintenance_types JSON text."
    ),
)

GET_MAINTENANCE_BY_ID = create_doc_entry(
    "Get one maintenance record",
    {
        200: success_entry(
            "Maintenance record",
            data={
                "id": "...",
                "reference": "MT-00001",
                "vehicle_id": "...",
                "maintenance_types": ["OIL_CHANGE"],
                "provider_type": "EXTERNAL",
                "date_from": "2026-04-01",
                "date_to": None,
                "cost": 150.0,
                "garage": "Quick Fit Motors",
                "notes": "Oil change",
                "recorded_by_id": "...",
                "created_at": "2026-04-01T10:00:00Z",
                "updated_at": "2026-04-01T10:00:00Z",
            },
        ),
        401: error_401_entry(),
        404: error_entry("Not found", code="NOT_FOUND", message="maintenance with id '...' not found"),
    },
    description="Fetch a single workshop maintenance row by its record id (`id` from POST or list). Returns 404 if the id does not exist or belongs to another vehicle.",
)

UPDATE_MAINTENANCE = create_doc_entry(
    "Update a maintenance record",
    {
        200: success_entry(
            "Maintenance record updated",
            data={
                "id": "...",
                "reference": "MT-00001",
                "vehicle_id": "...",
                "maintenance_types": ["MOT"],
                "provider_type": "EXTERNAL",
                "date_from": "2026-04-01",
                "date_to": "2026-04-03",
                "cost": 200.0,
                "garage": "Quick Fit Motors",
                "notes": None,
                "recorded_by_id": "...",
                "created_at": "2026-04-01T10:00:00Z",
                "updated_at": "2026-04-02T12:00:00Z",
            },
            message="Maintenance record updated",
        ),
        401: error_401_entry(),
        404: error_entry("Record not found", code="NOT_FOUND", message="maintenance with id '...' not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="At least one field must be provided"),
    },
    description=(
        "Partial JSON update. Changing dates or job types replaces the linked vehicle calendar row for this record "
        "(same source as POST maintenance). `date_from` cannot be in the future; `date_to` must be on or after `date_from` when set."
    ),
)

DELETE_MAINTENANCE = create_doc_entry(
    "Delete a maintenance record",
    {
        200: success_entry("Maintenance record deleted", message="Maintenance record removed"),
        401: error_401_entry(),
        404: error_entry("Record not found", code="NOT_FOUND", message="maintenance with id '...' not found"),
    },
    description="Removes the workshop row and its vehicle schedule calendar entry for this maintenance record.",
)

MAINTENANCE_COST_SUMMARY = create_doc_entry(
    "Maintenance spend by job type",
    {
        200: success_entry(
            "Cost summary",
            data={
                "vehicle_id": "...",
                "total_cost": 6695.0,
                "by_type": [
                    {"maintenance_type": "TYRE_REPLACEMENT", "cost": 3030.0, "percentage": 40.0},
                    {"maintenance_type": "MOT", "cost": 2030.0, "percentage": 30.0},
                    {"maintenance_type": "REPAIR", "cost": 2030.0, "percentage": 30.0},
                ],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
    },
    description="Total maintenance cost for the vehicle and how much each category (MOT, tyres, repairs, and so on) contributes.",
)

REPORT_DEFECT = create_doc_entry(
    "Report a defect",
    {
        201: custom_entry(
            "Defect saved; if any photos failed to upload they are listed separately",
            example={
                "success": True,
                "message": "Defect reported successfully",
                "data": {
                    "id": "...",
                    "reference": "DF-00042",
                    "vehicle_id": "...",
                    "route_id": "RT-652",
                    "reported_by": {"id": "user-uuid", "first_name": "Sam", "last_name": "Taylor"},
                    "status": "PENDING",
                    "description": "...",
                    "images": ["https://..."],
                    "allowed_to_drive": False,
                },
                "failed_images": [],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="defect_data must be valid JSON"),
    },
    description=(
        "Describe the problem in writing, attach optional photos, and submit in one step. "
        "You get links for photos that saved; if some files fail, the defect is still recorded and the failures are listed separately."
    ),
)

LIST_DEFECTS = create_doc_entry(
    "List reported defects",
    {
        200: success_entry(
            "Defects list",
            data={
                "items": [
                    {
                        "id": "...",
                        "reference": "DF-00042",
                        "vehicle_id": "...",
                        "route_id": "RT-652",
                        "reported_by": {"id": "user-uuid", "first_name": "Sam", "last_name": "Taylor"},
                        "status": "PENDING",
                        "category": "TYRES",
                        "images": ["https://..."],
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 20,
                "pages": 1,
            },
        ),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
    },
    description=(
        "Filter by defect workflow (pending, in progress, resolved). Search matches defect reference (DF- plus digits), reporter name, or route_id; "
        "use the status filters for workflow."
    ),
)

UPDATE_DEFECT = create_doc_entry(
    "Update a defect",
    {
        200: success_entry(
            "Defect updated",
            data={
                "id": "...",
                "reference": "DF-00042",
                "vehicle_id": "...",
                "route_id": "RT-652",
                "reported_by": {"id": "user-uuid", "first_name": "Sam", "last_name": "Taylor"},
                "status": "RESOLVED",
                "images": [],
                "allowed_to_drive": False,
            },
        ),
        401: error_401_entry(),
        404: error_entry("Defect not found", code="NOT_FOUND", message="defect with id '...' not found"),
        409: error_entry("Concurrent edit", code="CONFLICT", message="Defect was modified by another request"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="At least one field must be provided"),
    },
    description=(
        "Partial JSON update: status, allowed_to_drive, category, severity, description, route_id, "
        "reported_by_id, reported_at (not in the future). At least one field required."
    ),
)

DELETE_DEFECT = create_doc_entry(
    "Delete a defect",
    {
        200: success_entry("Defect deleted", message="Defect removed"),
        401: error_401_entry(),
        404: error_entry("Defect not found", code="NOT_FOUND", message="defect with id '...' not found"),
    },
    description="Permanently removes the defect and deletes any attached images from storage.",
)

ADD_SERVICE_RECORD = create_doc_entry(
    "Add a service record for a vehicle",
    {
        201: success_entry("Service record added", data={"id": "..."}, message="Service record saved"),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
    },
    description=(
        "Records `service_date`, `service_type`, and `cost` (typical form payload). "
        "`next_service_due` is optional: when omitted it is set from the vehicle’s configured `service_interval_months` "
        "(vehicle specs) as calendar months after `service_date`. "
        "`mileage_at_service` is optional: when omitted it defaults to the vehicle’s current odometer (`current_mileage`). "
        "Mileage-based reminders use `service_interval_miles` on the vehicle with `last_service_mileage`, which is synced from "
        "the latest service record after each add/update/delete. "
        "The vehicle row is updated so `next_service_due` is the maximum due date across all service records and "
        "`last_service_mileage` reflects the most recent service odometer reading."
    ),
)

LIST_SERVICE_RECORDS = create_doc_entry(
    "List service records for a vehicle",
    {
        200: success_entry("Service records", data={"items": [], "total": 0, "page": 1, "size": 20, "pages": 0}),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
    },
)

UPDATE_SERVICE_RECORD = create_doc_entry(
    "Update a service record",
    {
        200: success_entry(
            "Service record updated",
            data={
                "id": "...",
                "vehicle_id": "...",
                "service_date": "2025-01-15",
                "service_type": "FULL_SERVICE",
                "next_service_due": "2026-01-15",
                "mileage_at_service": 55200,
                "cost": 350.0,
                "status": "COMPLETED",
                "notes": "Garage notes",
                "created_at": "2025-01-15T10:00:00Z",
                "updated_at": "2025-01-16T12:00:00Z",
            },
            message="Service record updated",
        ),
        401: error_401_entry(),
        404: error_entry("Record not found", code="NOT_FOUND", message="service_record with id '...' not found"),
        422: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="next_service_due must be after service_date",
        ),
    },
    description=(
        "Partial update (JSON body). Send at least one of: `service_date`, `service_type`, `next_service_due`, "
        "`mileage_at_service`, `cost`, `status`, `notes`. `service_date` cannot be in the future. "
        "When `service_date` is changed and `next_service_due` is not sent, `next_service_due` is recomputed from the "
        "vehicle’s `service_interval_months` (same rule as create). "
        "If both `service_date` and `next_service_due` are set (after merge), `next_service_due` must be strictly after "
        "`service_date`. The vehicle’s cached `next_service_due` and `last_service_mileage` are refreshed from service "
        "records after the update."
    ),
)

DELETE_SERVICE_RECORD = create_doc_entry(
    "Delete a service record",
    {
        200: success_entry("Service record deleted", message="Service record removed"),
        401: error_401_entry(),
        404: error_entry("Record not found", code="NOT_FOUND", message="service_record with id '...' not found"),
    },
)

ADD_DOCUMENT = create_doc_entry(
    "Upload a compliance document",
    {
        201: success_entry(
            "Document uploaded",
            data={"id": "...", "title": "MOT Certificate", "document_type": "MOT"},
            message="Document uploaded successfully",
        ),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
        422: error_entry("Limit exceeded", code="VALIDATION_ERROR", message="Maximum 20 documents per vehicle"),
    },
    description=(
        "Attach a file such as MOT, tax, insurance, V5C, or general paperwork. "
        "Requires the usual admin JWT only (no vehicle document OTP step-up). "
        "Allowed types are capped per vehicle and file size; MOT, tax, and insurance updates refresh the vehicle’s compliance snapshot."
    ),
)

SEND_VEHICLE_DOC_OTP = create_doc_entry(
    "Request a vehicle document access OTP",
    {
        200: success_entry(
            "OTP sent",
            data={"message": "OTP sent to your registered email address. It expires in 10 minutes."},
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired access token"),
        403: error_entry(
            "Insufficient permission (Resource.VEHICLE_MANAGEMENT WRITE required)",
            code="FORBIDDEN",
            message="Insufficient permissions",
        ),
        422: error_entry(
            "Application rate limit: too many OTP sends for this user in the vehicle-doc scope",
            code="VALIDATION_ERROR",
            message="Too many OTP requests. Maximum 3 per 10 minutes. Please wait and try again.",
        ),
    },
    description=(
        "Sends a 6-digit OTP to the signed-in admin’s email. After verify, use the token only for listing or "
        "deleting vehicle documents."
    ),
)

VERIFY_VEHICLE_DOC_OTP = create_doc_entry(
    "Verify OTP and receive a vehicle document access token",
    {
        200: success_entry(
            "OTP verified - vehicle doc access token issued",
            data={
                "vehicle_doc_access_token": "a3f1c2e4b5d6..." * 4,
                "expires_in": 3600,
                "expires_at": "2026-04-01T13:00:00Z",
                "message": "OTP verified. Use X-Vehicle-Doc-Access-Token when listing or deleting vehicle documents.",
            },
        ),
        401: error_401_entry("AUTHENTICATION_ERROR", "Invalid or expired OTP"),
        403: error_entry(
            "Insufficient permission (Resource.VEHICLE_MANAGEMENT WRITE required)",
            code="FORBIDDEN",
            message="Insufficient permissions",
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
        "Returns a token to send as `X-Vehicle-Doc-Access-Token` when listing or deleting vehicle documents. "
        "Document uploads (POST) use the normal admin JWT only."
    ),
)

LIST_DOCUMENTS = create_doc_entry(
    "List vehicle documents",
    {
        200: success_entry("Documents", data=[{"id": "...", "title": "V5C Logbook"}]),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
    },
    description=(
        "Requires `X-Vehicle-Doc-Access-Token` from POST /v1/vehicles/documents/otp/verify in addition to the admin JWT."
    ),
)

DELETE_DOCUMENT = create_doc_entry(
    "Delete a vehicle document",
    {
        200: success_entry("Document deleted", message="Document removed"),
        401: error_401_entry(),
        404: error_entry("Document not found", code="NOT_FOUND", message="document with id '...' not found"),
    },
    description=(
        "Requires `X-Vehicle-Doc-Access-Token` from POST /v1/vehicles/documents/otp/verify in addition to the admin JWT."
    ),
)

LIST_IMAGES = create_doc_entry(
    "List vehicle photos",
    {
        200: success_entry("Temporary link for each stored photo", data=["https://imagedelivery.net/...", "https://imagedelivery.net/..."]),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
    },
    description="Returns time-limited links to view each stored photo.",
)

UPLOAD_IMAGES = create_doc_entry(
    "Upload vehicle photos",
    {
        200: custom_entry(
            "Photo links for successful uploads; any failures are listed separately",
            example={
                "success": True,
                "message": "2 image(s) uploaded successfully",
                "data": ["https://imagedelivery.net/...", "https://imagedelivery.net/..."],
                "failed_images": [],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
    },
    description=(
        "Add up to ten images at once. You get back a link for each file that saved successfully. "
        "Failed files are listed with a short reason without blocking the rest. "
        "When registering a brand-new vehicle, failed file details may be described a little differently in the response."
    ),
)

DELETE_IMAGE = create_doc_entry(
    "Delete a vehicle image",
    {
        200: success_entry("Image deleted", message="Image removed"),
        401: error_401_entry(),
        404: error_entry("Image not found", code="NOT_FOUND", message="image with id '...' not found"),
    },
)

# Drafts

SAVE_DRAFT = create_doc_entry(
    "Save vehicle as draft",
    {
        201: custom_entry(
            "Draft saved with any uploaded images/documents; failures listed separately",
            example={
                "success": True,
                "message": "Draft saved successfully",
                "data": {
                    "id": "draft-uuid",
                    "draft_number": "DR-001",
                    "vehicle_id": "vehicle-uuid",
                    "registration_number": "MK24 XYP",
                    "fleet_number": "VAN-042",
                    "make": None,
                    "model": None,
                    "year": None,
                    "vehicle_type": "INTERNAL",
                    "fuel_type": "DIESEL",
                    "images": [{"id": "img-uuid", "url": "https://..."}],
                    "documents": [
                        {"id": "doc-uuid", "document_type": "MOT", "title": "MOT Certificate", "url": "https://..."},
                    ],
                },
                "failed_documents": [],
                "failed_images": [],
            },
        ),
        401: error_401_entry(),
        409: error_entry("Duplicate registration", code="CONFLICT", message="Vehicle with registration '...' already exists"),
        422: error_entry("Validation error", code="VALIDATION_ERROR", message="At least one field must be provided to save a draft"),
    },
    description=(
        "Creates a vehicle in DRAFT status with partial data. Send as multipart/form-data.\n\n"
        "While in draft, `initial_maintenance` (when IN_MAINTENANCE) is stored only as a **preview** maintenance row for "
        "GET responses — no vehicle schedule/calendar rows are written until publish.\n\n"
        "**Form fields:**\n"
        "- `vehicle_data` (required): JSON string with vehicle fields; `max_continuous_driving_hours` and "
        "`break_duration_minutes` are required; other vehicle fields are optional. `availability` defaults to "
        "ACTIVE when omitted. When `availability` is IN_MAINTENANCE, `initial_maintenance` is required (same rules as "
        "create vehicle).\n"
        "- `images` (optional): Up to 2 image files.\n"
        "- `documents` (optional): Up to 5 document files.\n"
        "- `documents_metadata` (optional, required when documents are sent): JSON array of metadata objects, one per document file matched by index.\n\n"
        "Response returns the full draft state including all images and documents with their IDs."
    ),
)

UPDATE_DRAFT = create_doc_entry(
    "Update a vehicle draft",
    {
        200: custom_entry(
            "Draft updated; full current state returned",
            example={
                "success": True,
                "message": "Draft updated successfully",
                "data": {
                    "id": "draft-uuid",
                    "draft_number": "DR-001",
                    "vehicle_id": "vehicle-uuid",
                    "registration_number": "MK24 XYP",
                    "make": "Ford",
                    "model": "Transit",
                    "images": [{"id": "img-uuid-2", "url": "https://..."}],
                    "documents": [
                        {"id": "doc-uuid-2", "document_type": "INSURANCE", "title": "Insurance Policy", "url": "https://..."},
                    ],
                },
                "failed_documents": [],
                "failed_images": [],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Draft not found", code="NOT_FOUND", message="vehicle_draft with id '...' not found"),
        409: error_entry("Duplicate registration", code="CONFLICT", message="Vehicle with registration '...' already exists"),
    },
    description=(
        "Updates a draft. Send as multipart/form-data. The vehicle must still be in DRAFT status.\n\n"
        "**Form fields:**\n"
        "- `vehicle_data` (required): JSON string with fields to update. Can be `{}` if only managing files.\n"
        "- `images` (optional): New image files to add (up to 2 per request).\n"
        "- `documents` (optional): New document files to add.\n"
        "- `documents_metadata` (optional, required when documents are sent): JSON array matching new document files by index.\n"
        '- `deleted_image_ids` (optional): JSON array of image IDs to remove, e.g. `["img-uuid-1"]`.\n'
        '- `deleted_document_ids` (optional): JSON array of document IDs to remove, e.g. `["doc-uuid-1"]`.\n'
        "- `updated_documents_metadata` (optional): JSON array of metadata patches for existing documents. "
        "Each object must have `id` plus at least one field to change, "
        'e.g. `[{"id":"doc-uuid","expiry_date":"2028-01-01"}]`.\n\n'
        "**Processing order:** (1) deletions, (2) metadata updates, (3) new uploads, (4) return full state.\n\n"
        "**To update only metadata** (e.g. changed expiry date, same file): use `updated_documents_metadata`.\n"
        "**To replace the file** (new file, same or different metadata): put the old ID in `deleted_document_ids` "
        "and the replacement in `documents` + `documents_metadata`.\n"
        "**To replace both file and metadata**: same as above (delete + recreate).\n\n"
        "Response always returns the full current state of images and documents after all changes."
    ),
)

GET_DRAFT = create_doc_entry(
    "Get a vehicle draft",
    {
        200: success_entry(
            "Full draft state including images and documents",
            data={
                "id": "draft-uuid",
                "draft_number": "DR-001",
                "vehicle_id": "vehicle-uuid",
                "registration_number": "MK24 XYP",
                "fleet_number": "VAN-042",
                "make": "Ford",
                "model": None,
                "images": [{"id": "img-uuid", "url": "https://..."}],
                "documents": [
                    {"id": "doc-uuid", "document_type": "MOT", "title": "MOT Certificate", "url": "https://..."},
                ],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Draft not found", code="NOT_FOUND", message="vehicle_draft with id '...' not found"),
    },
    description=(
        "Returns all saved vehicle fields, images (with IDs and URLs), and documents (with IDs, metadata, and URLs). "
        "Use the image and document IDs when calling PATCH to delete or replace them."
    ),
)

LIST_DRAFTS = create_doc_entry(
    "List vehicle drafts",
    {
        200: success_entry(
            "Drafts for the current page",
            data={
                "items": [
                    {
                        "id": "draft-uuid",
                        "draft_number": "DR-001",
                        "vehicle_id": "vehicle-uuid",
                        "registration_number": "MK24 XYP",
                        "fleet_number": "VAN-042",
                        "fleet_custom_name": "Eagle Van",
                        "preferred_driver": {"id": "user-uuid", "first_name": "Sam", "last_name": "Taylor"},
                        "make": "Ford",
                        "model": "Transit Custom",
                        "year": 2020,
                        "vehicle_type": "INTERNAL",
                        "fuel_type": "PETROL",
                        "average_mpg": 35.0,
                        "range_miles": None,
                        "cargo_volume_m3": 10.0,
                        "max_payload_kg": 1000.0,
                        "service_interval_miles": 10000,
                        "service_interval_months": 12,
                        "max_continuous_driving_hours": 4.0,
                        "break_duration_minutes": 30,
                        "availability": "ACTIVE",
                        "last_edited": "2026-05-11T12:00:00Z",
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 20,
                "pages": 1,
            },
        ),
        401: error_401_entry(),
    },
    description=(
        "Paginated list of vehicle drafts that are not yet published (`Vehicle.status` DRAFT). Each item mirrors the "
        "list columns used in admin (registration, fleet number, names, driver, make/model/year, type, fuel, MPG, "
        "range, cargo, payload, service intervals, driving/break fields, availability, and `last_edited` as the later "
        "of draft vs linked vehicle `updated_at`). Use GET draft for `initial_maintenance` when needed. "
        "Query: `page`, `size`, optional `order_desc` (default `true`: newest `created_at` first, matching Figma; "
        "`false` for oldest first), and optional `search` (case-insensitive substring on registration, fleet number, "
        "make, model, year as text, vehicle type). Tie-breaker: draft `id`."
    ),
)

DELETE_DRAFT = create_doc_entry(
    "Delete a vehicle draft",
    {
        200: success_entry("Draft deleted", message="Draft removed successfully"),
        401: error_401_entry(),
        404: error_entry("Draft not found", code="NOT_FOUND", message="vehicle_draft with id '...' not found"),
        422: error_entry("Already published", code="VALIDATION_ERROR", message="Cannot delete a draft that has already been published"),
    },
    description="Permanently removes the draft and its associated vehicle record (including uploaded images and documents). Only works for vehicles still in DRAFT status.",
)

PUBLISH_DRAFT = create_doc_entry(
    "Publish a vehicle draft",
    {
        200: custom_entry(
            "Draft published as a full vehicle",
            example={
                "success": True,
                "message": "Vehicle published successfully",
                "data": {
                    "id": "vehicle-uuid",
                    "fleet_number": "VAN-042",
                    "registration_number": "MK24 XYP",
                    "availability": "ACTIVE",
                    "images": [{"id": "img-uuid", "url": "https://..."}],
                    "documents": [{"id": "doc-uuid", "title": "MOT Certificate", "document_type": "MOT"}],
                },
                "failed_documents": [],
                "failed_images": [],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Draft not found", code="NOT_FOUND", message="vehicle_draft with id '...' not found"),
        422: error_entry(
            "Validation failed",
            code="VALIDATION_ERROR",
            message="Cannot publish draft — 2 validation errors for CreateVehicleRequest ...",
        ),
    },
    description=(
        "Publishes the draft as a full vehicle. Send as multipart/form-data.\n\n"
        "The incoming `vehicle_data` is merged with the saved draft state, then validated against "
        "CreateVehicleRequest. If the merged availability is IN_MAINTENANCE and `initial_maintenance` is not in the "
        "request but a preview maintenance row exists on the draft vehicle, that row is merged in for validation only. "
        "Any draft preview maintenance row is removed before the vehicle is activated; availability and maintenance "
        "then follow the same schedule rules as POST /vehicles (create). If any required fields are missing or "
        "invalid, a 422 error is returned and **nothing is written** to the database.\n\n"
        "**Form fields:**\n"
        "- `vehicle_data` (required): JSON string with any final field updates including `availability` "
        "(ACTIVE | UNAVAILABLE | IN_MAINTENANCE) and optionally `initial_maintenance` when IN_MAINTENANCE. "
        "Can be `{}` if all data was already saved via PATCH.\n"
        "- `images`, `documents`, `documents_metadata` — optional, new files to upload (up to 2 images and up to 5 documents per request).\n"
        "- `deleted_image_ids`, `deleted_document_ids` — optional, JSON arrays of UUIDs to remove.\n"
        "- `updated_documents_metadata` — optional, metadata-only updates for existing documents.\n\n"
        "**Processing order:** (1) validate merged data against CreateVehicleRequest, "
        "(2) publish (set status=ACTIVE, apply availability + schedule entries), "
        "(3) deletions, (4) metadata updates, (5) new uploads.\n\n"
        "**Required vehicle fields:** registration_number, fleet_custom_name, make, model, year, "
        "cargo_volume_m3, max_payload_kg, service_interval_miles, service_interval_months, "
        "max_continuous_driving_hours (0–24), break_duration_minutes (0–480), "
        "and either average_mpg (non-electric) or range_miles (electric). "
        "Expiry dates (if set) must not be in the past."
    ),
)
