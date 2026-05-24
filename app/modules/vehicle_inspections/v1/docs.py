from __future__ import annotations

from app.core.swagger import create_doc_entry, custom_entry, error_401_entry, error_entry, success_entry

GET_ASSIGNED_VEHICLE = create_doc_entry(
    "Get driver's assigned vehicle",
    {
        200: success_entry(
            "Assigned vehicle",
            data={"id": "...", "registration_number": "AB21 XYX", "make": "Ford", "model": "Transit Custom"},
        ),
        401: error_401_entry(),
        422: error_entry("No vehicle assigned", code="VALIDATION_ERROR", message="No vehicle is currently assigned to you"),
    },
    description="Returns the vehicle assigned to the authenticated driver.",
)

LOOKUP_VEHICLE = create_doc_entry(
    "Look up vehicle by registration",
    {
        200: success_entry("Vehicle found", data={"id": "...", "registration_number": "AB21 XYX"}),
        401: error_401_entry(),
        404: error_entry("Not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
        422: error_entry("Not assigned", code="VALIDATION_ERROR", message="This vehicle is not assigned to you"),
    },
    description="Look up vehicle by plate. Verifies it is assigned to the authenticated driver.",
)

CREATE_INSPECTION = create_doc_entry(
    "Start an inspection",
    {
        201: success_entry(
            "Inspection created with IN_PROGRESS status",
            data={
                "id": "...",
                "status": "IN_PROGRESS",
                "checklist_status": [
                    {"category": "INSIDE_CABIN", "label": "Inside Cabin Check", "completed": True},
                    {"category": "OUTSIDE_VEHICLE", "label": "Outside Vehicle Check", "completed": True},
                    {"category": "LOAD_EQUIPMENT", "label": "Load & Equipment Check", "completed": True},
                ],
                "defects": [],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Vehicle not found", code="NOT_FOUND", message="vehicle with id '...' not found"),
        422: error_entry("Not assigned", code="VALIDATION_ERROR", message="This vehicle is not assigned to you"),
    },
    description=(
        "Creates an inspection record with checklist data. Status starts as IN_PROGRESS. "
        "After this, the driver can report defects and eventually sign to finalize."
    ),
)

REPORT_DEFECT = create_doc_entry(
    "Report a defect during inspection",
    {
        201: custom_entry(
            "Defect created and linked to the inspection",
            example={
                "success": True,
                "message": "Defect reported",
                "data": {
                    "id": "...",
                    "reference": "DF-00042",
                    "category": "TYRES",
                    "severity": "MINOR",
                    "status": "PENDING",
                    "description": "Driver-side mirror cracked.",
                    "images": ["https://..."],
                },
            },
        ),
        401: error_401_entry(),
        404: error_entry("Inspection not found", code="NOT_FOUND", message="vehicle_inspection with id '...' not found"),
        422: error_entry("Already finalized", code="VALIDATION_ERROR", message="Cannot report defects on a finalized inspection"),
    },
    description=(
        "Report a defect during an in-progress inspection. Send as multipart/form-data with:\n\n"
        "- **defect_data** (required): JSON string with category, severity, description\n"
        "- **images** (required): One or more photo files\n\n"
        "Creates a VehicleDefect record linked to this inspection. Can be called multiple times."
    ),
)

GET_INSPECTION = create_doc_entry(
    "Get inspection summary",
    {
        200: success_entry(
            "Full inspection with checklist status and any reported defects",
            data={
                "id": "...",
                "status": "IN_PROGRESS",
                "checklist_status": [{"category": "INSIDE_CABIN", "label": "Inside Cabin Check", "completed": True}],
                "defects": [{"id": "...", "reference": "DF-00042", "category": "TYRES", "severity": "MINOR", "status": "PENDING"}],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Not found", code="NOT_FOUND", message="vehicle_inspection with id '...' not found"),
    },
    description="Returns the inspection summary for the driver to review before signing.",
)

SIGN_INSPECTION = create_doc_entry(
    "Sign and finalize inspection",
    {
        200: custom_entry(
            "Inspection finalized",
            example={
                "success": True,
                "message": "Inspection completed — vehicle marked safe",
                "data": {
                    "id": "...",
                    "result": "PASS",
                    "status": "COMPLETED",
                    "declaration_accepted": True,
                    "signature_url": "https://...",
                },
            },
        ),
        401: error_401_entry(),
        404: error_entry("Not found", code="NOT_FOUND", message="vehicle_inspection with id '...' not found"),
        422: error_entry("Already finalized", code="VALIDATION_ERROR", message="Inspection has already been finalized"),
    },
    description=(
        "Finalize the inspection with declaration and signature. Send as multipart/form-data with:\n\n"
        "- **sign_data** (required): JSON string with declaration_accepted: true\n"
        "- **signature** (required): Signature image file\n\n"
        "If no defects were reported: result=PASS, status=COMPLETED — driver can start driving.\n"
        "If defects were reported: result=FAIL, status=AWAITING_RESOLUTION — poll /status until resolved."
    ),
)

GET_INSPECTION_STATUS = create_doc_entry(
    "Poll inspection defect resolution status",
    {
        200: success_entry(
            "Current resolution status",
            data={
                "inspection_id": "...",
                "status": "AWAITING_RESOLUTION",
                "total_defects": 2,
                "resolved_defects": 1,
                "allowed_to_drive_count": 0,
                "can_proceed": False,
            },
        ),
        401: error_401_entry(),
        404: error_entry("Not found", code="NOT_FOUND", message="vehicle_inspection with id '...' not found"),
    },
    description=(
        "Polling endpoint for the driver. Returns defect resolution progress.\n\n"
        "When all defects are either RESOLVED or have allowed_to_drive=true, "
        "the inspection auto-transitions to RESOLVED and can_proceed becomes true."
    ),
)

GET_PENDING_INSPECTION_STATUS = create_doc_entry(
    "Get pending inspection status for assigned route",
    {
        200: success_entry(
            "Current pending inspection status",
            data={
                "inspection_id": "...",
                "status": "IN_PROGRESS",
                "total_defects": 1,
                "resolved_defects": 0,
                "allowed_to_drive_count": 0,
                "can_proceed": False,
            },
        ),
        401: error_401_entry(),
        404: error_entry("Not found", code="NOT_FOUND", message="vehicle_inspection with id 'pending' not found"),
        422: error_entry(
            "Missing assignment",
            code="VALIDATION_ERROR",
            message="No route is currently assigned to this vehicle",
        ),
    },
    description=(
        "Returns status for the pending in-progress inspection of the authenticated driver's "
        "currently assigned vehicle route."
    ),
)

GET_LATEST_TRIP_INSPECTION_STATUS = create_doc_entry(
    "Get latest non in-progress trip inspection status",
    {
        200: success_entry(
            "Latest trip inspection status",
            data={
                "inspection_id": "...",
                "status": "COMPLETED",
                "total_defects": 1,
                "resolved_defects": 1,
                "allowed_to_drive_count": 0,
                "can_proceed": True,
            },
        ),
        401: error_401_entry(),
    },
    description=(
        "Returns the latest trip inspection status for the authenticated driver where status "
        "is not IN_PROGRESS (AWAITING_RESOLUTION, RESOLVED, or COMPLETED). "
        "If no qualifying inspection exists yet, returns success with no data."
    ),
)

DELETE_INSPECTION = create_doc_entry(
    "Delete inspection",
    {
        200: success_entry("Inspection deleted successfully"),
        401: error_401_entry(),
        404: error_entry("Not found", code="NOT_FOUND", message="vehicle_inspection with id '...' not found"),
    },
    description="Deletes an inspection record by id for the authenticated driver.",
)
