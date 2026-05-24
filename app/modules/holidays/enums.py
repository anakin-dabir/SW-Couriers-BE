"""Holiday-related enums."""

import enum


class HolidayAudience(enum.StrEnum):
    """Audience for a holiday: which drivers it applies to."""

    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"
    BOTH = "BOTH"
