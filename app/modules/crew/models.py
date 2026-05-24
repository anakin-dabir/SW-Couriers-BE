from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.common.models import BaseModel


class Crew(BaseModel):
    """Temporal pairing of one driver (a user with DRIVER role) with one vehicle.

    All FKs use ``ON DELETE SET NULL`` so historical crew rows survive even
    if the referenced user/vehicle is later hard-deleted. A ``NULL`` ref is
    the deleted-entity placeholder.
    """

    __tablename__ = "crews"

    driver_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_crews_driver_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    vehicle_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("vehicles.id", name="fk_crews_vehicle_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    started_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_crews_started_by_id", ondelete="SET NULL"),
        nullable=True,
    )
    ended_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_crews_ended_by_id", ondelete="SET NULL"),
        nullable=True,
    )

    end_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    driver = relationship("User", lazy="raise", foreign_keys=[driver_id])
    vehicle = relationship("Vehicle", lazy="raise", foreign_keys=[vehicle_id])

    __table_args__ = (
        Index(
            "uq_crews_open_driver",
            "driver_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL AND driver_id IS NOT NULL"),
        ),
        Index(
            "uq_crews_open_vehicle",
            "vehicle_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL AND vehicle_id IS NOT NULL"),
        ),
        Index("ix_crews_ended_at", "ended_at"),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_crews_time_window",
        ),
    )


class RouteCrewAssignment(BaseModel):
    """Assignment of a crew to a route (one open per route, one open per crew).

    ``route_id`` cascades on delete (an assignment without a route is meaningless).
    ``crew_id`` is ``RESTRICT`` — crews are never hard-deleted, only closed via
    ``ended_at``, so this acts as a defensive guard against accidental purges.
    ``assigned_by_id`` / ``unassigned_by_id`` use ``SET NULL`` so the history
    row survives if the acting admin is later removed.
    """

    __tablename__ = "route_crew_assignments"

    route_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("routes.id", name="fk_rca_route_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    crew_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("crews.id", name="fk_rca_crew_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    unassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    assigned_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_rca_assigned_by_id", ondelete="SET NULL"),
        nullable=True,
    )
    unassigned_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_rca_unassigned_by_id", ondelete="SET NULL"),
        nullable=True,
    )

    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)

    crew = relationship("Crew", lazy="raise", foreign_keys=[crew_id])

    __table_args__ = (
        Index(
            "uq_rca_open_per_route",
            "route_id",
            unique=True,
            postgresql_where=text("unassigned_at IS NULL"),
        ),
        Index(
            "uq_rca_open_per_crew",
            "crew_id",
            unique=True,
            postgresql_where=text("unassigned_at IS NULL"),
        ),
        Index("ix_rca_unassigned_at", "unassigned_at"),
        CheckConstraint(
            "unassigned_at IS NULL OR unassigned_at >= assigned_at",
            name="ck_rca_time_window",
        ),
    )
