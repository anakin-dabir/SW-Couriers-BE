"""Inspection enums."""

import enum


class InspectionType(enum.StrEnum):
    PRE_TRIP = "PRE_TRIP"
    POST_TRIP = "POST_TRIP"


class InspectionResult(enum.StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class InspectionStatus(enum.StrEnum):
    """Lifecycle of an inspection.

    IN_PROGRESS         — created, driver filling checklist / reporting defects
    COMPLETED           — signed with no defects, driver can proceed
    AWAITING_RESOLUTION — signed with defects, waiting for admin to resolve/allow
    RESOLVED            — all defects resolved or allowed_to_drive set
    """

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    AWAITING_RESOLUTION = "AWAITING_RESOLUTION"
    RESOLVED = "RESOLVED"


class ChecklistCategory(enum.StrEnum):
    INSIDE_CABIN = "INSIDE_CABIN"
    OUTSIDE_VEHICLE = "OUTSIDE_VEHICLE"
    LOAD_EQUIPMENT = "LOAD_EQUIPMENT"
