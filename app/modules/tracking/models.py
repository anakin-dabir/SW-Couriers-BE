"""GPS waypoint model. TimescaleDB hypertable for high-volume telemetry.

Waypoints are bulk-inserted from driver app batch submissions.
Compression at 7 days, retention at 90 days (GDPR storage limitation).
The hypertable conversion and policies are handled in the migration.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import Base, UUIDMixin, _utc_now


class Waypoint(Base, UUIDMixin):
    """GPS telemetry point — stored in TimescaleDB hypertable.

    No version column (never updated). Has both created_at (server insert time)
    and recorded_at (device-side timestamp used as hypertable partition column).
    """

    __tablename__ = "waypoints"

    driver_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Position ─────────────────────────────
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    altitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)  # meters
    speed: Mapped[float | None] = mapped_column(Float, nullable=True)  # m/s
    heading: Mapped[float | None] = mapped_column(Float, nullable=True)  # degrees

    # ── Time (hypertable partition column) ───
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    # Server-side insert timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        server_default=func.now(),
        nullable=False,
    )

    # ── Context ──────────────────────────────
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="gps")  # gps, network, manual

    def __repr__(self) -> str:
        return f"<Waypoint driver={self.driver_id} lat={self.latitude} lng={self.longitude}>"
