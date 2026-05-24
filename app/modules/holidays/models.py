"""Holiday ORM models."""

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.models import BaseModel, BaseModelNoVersion
from app.modules.holidays.enums import HolidayAudience


class Holiday(BaseModel):
    """Holiday definition for driver scheduling."""

    __tablename__ = "holidays"

    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # INTERNAL / EXTERNAL / BOTH
    audience: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=HolidayAudience.BOTH,
        doc="HolidayAudience value: INTERNAL, EXTERNAL, or BOTH.",
    )

    # If true, specific drivers can be allowed to work during this holiday.
    allow_shifts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    allowed_drivers: Mapped[list["HolidayAllowedDriver"]] = relationship(
        "HolidayAllowedDriver",
        back_populates="holiday",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Holiday year={self.year} name={self.name!r} {self.start_date}–{self.end_date}>"


class HolidayAllowedDriver(BaseModelNoVersion):
    """Join table: drivers explicitly allowed to work on a given holiday."""

    __tablename__ = "holiday_allowed_drivers"
    __table_args__ = (UniqueConstraint("holiday_id", "driver_id", name="uq_holiday_allowed_driver_holiday_driver"),)

    holiday_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("holidays.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    driver_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    holiday: Mapped[Holiday] = relationship("Holiday", back_populates="allowed_drivers", lazy="raise")

    def __repr__(self) -> str:
        return f"<HolidayAllowedDriver holiday={self.holiday_id} driver={self.driver_id}>"
