"""Display metadata for team availability (leave types)."""

from __future__ import annotations

from app.modules.drivers.enums import TimeOffType

# Hex colours aligned with admin Team Availability Figma filters.
LEAVE_TYPE_DISPLAY: dict[TimeOffType, dict[str, str]] = {
    TimeOffType.ANNUAL_LEAVE: {"label": "Annual Leave", "color_hex": "#7C3AED"},
    TimeOffType.SICK_LEAVE: {"label": "Sick Leave", "color_hex": "#DC2626"},
    TimeOffType.MEDICAL_APPOINTMENT: {"label": "Medical Appointment", "color_hex": "#0D9488"},
    TimeOffType.FAMILY_PARENTAL_LEAVE: {"label": "Family & Parental Leave", "color_hex": "#2563EB"},
    TimeOffType.PATERNITY_LEAVE: {"label": "Paternity Leave", "color_hex": "#EA580C"},
    TimeOffType.MATERNITY_LEAVE: {"label": "Maternity Leave", "color_hex": "#DB2777"},
    TimeOffType.ADOPTION_LEAVE: {"label": "Adoption Leave", "color_hex": "#9333EA"},
    TimeOffType.CIVIC_STATUTORY_DUTIES: {"label": "Civic & Statutory Duties", "color_hex": "#171717"},
    TimeOffType.DISCRETIONARY_SPECIAL_LEAVE: {"label": "Discretionary & Special Leave", "color_hex": "#CA8A04"},
    TimeOffType.EMERGENCY_LEAVE: {"label": "Emergency Leave", "color_hex": "#B45309"},
}

MAX_CALENDAR_RANGE_DAYS = 93
MAX_WHO_IS_OFF_RANGE_DAYS = 14
MAX_CALENDAR_DAY_ENTRIES = 10_000

# Roles allowed to use My Leaves (internal staff leave).
STAFF_LEAVE_ROLES = frozenset({"ADMIN", "SUPER_ADMIN"})
