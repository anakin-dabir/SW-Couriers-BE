"""OpenAPI docs for team availability (admin settings)."""

from app.core.swagger import create_doc_entry, error_401_entry, error_entry, success_entry

TEAM_AVAILABILITY_CALENDAR = create_doc_entry(
    "Team calendar — driver leave + public holidays for admin Team Availability screen",
    {
        200: success_entry(
            "Calendar payload",
            data={
                "from_date": "2026-04-01",
                "to_date": "2026-04-30",
                "summary": {
                    "drivers_on_leave_count": 5,
                    "staff_on_leave_count": 2,
                    "leave_day_entries_count": 12,
                    "holiday_day_entries_count": 2,
                },
                "leave_entries": [],
                "holiday_entries": [],
            },
        ),
        400: error_entry(
            "Invalid date range or filters",
            code="VALIDATION_ERROR",
            message="from_date cannot be after to_date",
        ),
        401: error_401_entry(),
        403: error_entry(
            "Insufficient permission",
            code="FORBIDDEN",
            message="DRIVERS read permission required",
        ),
    },
)

TEAM_AVAILABILITY_WHO_IS_OFF = create_doc_entry(
    "Who is off — sidebar list for a date range (max 14 days)",
    {
        200: success_entry(
            "Who is off",
            data={
                "from_date": "2026-04-20",
                "to_date": "2026-04-26",
                "total": 2,
                "items": [],
            },
        ),
        400: error_entry(
            "Invalid date range",
            code="VALIDATION_ERROR",
            message="Date range cannot exceed 14 days",
        ),
        401: error_401_entry(),
        403: error_entry(
            "Insufficient permission",
            code="FORBIDDEN",
            message="DRIVERS read permission required",
        ),
    },
)

TEAM_AVAILABILITY_LEAVE_DETAIL = create_doc_entry(
    "Leave details modal — pass member_type=DRIVER (fleet) or STAFF (admin My Leaves)",
    {
        200: success_entry("Leave detail", data={}),
        401: error_401_entry(),
        403: error_entry(
            "Insufficient permission",
            code="FORBIDDEN",
            message="DRIVERS read permission required",
        ),
        404: error_entry(
            "Time off not found",
            code="NOT_FOUND",
            message="driver_time_off or staff_time_off not found",
        ),
    },
)

TEAM_AVAILABILITY_MY_LEAVE_LIST = create_doc_entry(
    "My Leaves table — authenticated admin/super-admin personal leave",
    {
        200: success_entry("My leave list", data={"items": [], "paid_leave_taken": 0, "unpaid_leave_taken": 0, "total": 0}),
        401: error_401_entry(),
        403: error_entry(
            "Insufficient permission",
            code="FORBIDDEN",
            message="SETTINGS read permission required",
        ),
    },
)

TEAM_AVAILABILITY_MY_LEAVE_CREATE = create_doc_entry(
    "Apply for leave (My Leaves modal)",
    {
        201: success_entry("Leave created", data={}),
        400: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="Leave dates overlap an existing entry.",
        ),
        401: error_401_entry(),
        403: error_entry(
            "Insufficient permission",
            code="FORBIDDEN",
            message="SETTINGS write permission required",
        ),
    },
)

TEAM_AVAILABILITY_MY_LEAVE_DETAIL = create_doc_entry(
    "Single My Leaves row (edit modal prefill)",
    {
        200: success_entry("My leave", data={}),
        401: error_401_entry(),
        403: error_entry(
            "Insufficient permission",
            code="FORBIDDEN",
            message="SETTINGS read or ownership required",
        ),
        404: error_entry(
            "Not found",
            code="NOT_FOUND",
            message="staff_time_off not found",
        ),
    },
)

TEAM_AVAILABILITY_MY_LEAVE_UPDATE = create_doc_entry(
    "Edit leave (My Leaves modal)",
    {
        200: success_entry("Leave updated", data={}),
        400: error_entry(
            "Validation error",
            code="VALIDATION_ERROR",
            message="end_date cannot be before start_date",
        ),
        401: error_401_entry(),
        403: error_entry(
            "Forbidden",
            code="FORBIDDEN",
            message="You can only manage your own leave requests.",
        ),
        404: error_entry("Not found", code="NOT_FOUND", message="staff_time_off not found"),
    },
)

TEAM_AVAILABILITY_MY_LEAVE_DELETE = create_doc_entry(
    "Delete leave (My Leaves confirmation)",
    {
        204: {"description": "Leave deleted"},
        401: error_401_entry(),
        403: error_entry(
            "Forbidden",
            code="FORBIDDEN",
            message="You can only manage your own leave requests.",
        ),
        404: error_entry("Not found", code="NOT_FOUND", message="staff_time_off not found"),
    },
)

TEAM_AVAILABILITY_LEAVE_TYPES = create_doc_entry(
    "Leave type metadata for filter dropdown (label + colour)",
    {
        200: success_entry("Leave types", data={"items": []}),
        401: error_401_entry(),
        403: error_entry(
            "Insufficient permission",
            code="FORBIDDEN",
            message="DRIVERS read permission required",
        ),
    },
)
