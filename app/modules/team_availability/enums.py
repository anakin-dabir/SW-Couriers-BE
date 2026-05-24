"""Team availability enums."""

from __future__ import annotations

import enum


class TeamMemberType(enum.StrEnum):
    """Leave owner category on the shared team calendar."""

    DRIVER = "DRIVER"
    STAFF = "STAFF"


class LeavePaymentStatus(enum.StrEnum):
    """UI label for paid vs unpaid leave."""

    PAID = "PAID"
    UNPAID = "UNPAID"
