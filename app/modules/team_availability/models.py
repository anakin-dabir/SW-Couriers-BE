"""ORM models for team availability (staff/admin leave)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import BaseModelNoVersion
from app.modules.drivers.enums import TimeOffType


class StaffTimeOff(BaseModelNoVersion):
    """Personal leave for internal staff (admin / super-admin) — My Leaves tab."""

    __tablename__ = "staff_time_off"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default=TimeOffType.ANNUAL_LEAVE,
        doc="TimeOffType value",
    )
    days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user = relationship("User", lazy="raise", foreign_keys=[user_id])
