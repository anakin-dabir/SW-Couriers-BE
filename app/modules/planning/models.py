"""Route planning models. Route plans, routes, route stops, and planning audit log.

Route plans contain multiple routes (one per driver). Each route has
ordered stops. The planning engine optimizes stop assignment and ordering.

**Navigation polylines** on ``Route`` are written by planning or a background job after the
ordered ``route_stops`` for that row are final (or after replan); see comments on those columns.
"""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import Sequence

from app.common.models import AppendOnlyModel, Base, BaseModel, BaseModelNoVersion
from app.modules.planning.enums import (
    RoutePlanStatus,
    RouteStatus,
    RouteStopFlowType,
    RouteStopStatus,
    RouteType,
    StopAssignmentSource,
)

route_code_seq = Sequence("route_code_seq", metadata=Base.metadata)


class RoutePlan(BaseModel):
    """A route plan for a service day. Contains multiple routes.

    ``service_date`` is the **local calendar date** for this depot's day of operation: it must
    be computed using the same **IANA timezone** as ``Depot.timezone`` when plans are created
    (planner / ops APIs). That keeps ``service_date`` aligned with driver ``GET …/routes/today``,
    which resolves "today" in the driver's depot zone by default.
    """

    __tablename__ = "route_plans"

    __table_args__ = (UniqueConstraint("depot_id", "service_date", name="uq_route_plans_depot_date"),)

    service_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
        doc="Depot-local calendar date (interpret with this row's depot IANA ``timezone``).",
    )
    depot_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("depots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[RoutePlanStatus] = mapped_column(
        Enum(RoutePlanStatus, native_enum=False),
        nullable=False,
        default=RoutePlanStatus.DRAFT,
        server_default=RoutePlanStatus.DRAFT.value,
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationships ────────────────────────
    depot = relationship("Depot", lazy="raise", foreign_keys=[depot_id])

    def __repr__(self) -> str:
        return f"<RoutePlan {self.service_date} depot={self.depot_id} status={self.status}>"


class Route(BaseModel):
    """A single driver's route, optionally attached to a day plan.

    Cached navigation fields (polyline / meta / fingerprint) are optional until a planner or
    async job fills them after route build or stop-sequence changes; see module docstring.
    """

    __tablename__ = "routes"

    plan_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("route_plans.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    driver_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    vehicle_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("vehicles.id", ondelete="SET NULL"),
        nullable=True,
    )

    route_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'RT-' || lpad(nextval('{route_code_seq.name}')::text, 3, '0')"),
        doc="Human-friendly route id in format RT-NNN",
    )
    route_type: Mapped[RouteType] = mapped_column(
        Enum(RouteType, native_enum=False),
        nullable=False,
        default=RouteType.DELIVERY,
        server_default=RouteType.DELIVERY.value,
        index=True,
        doc="Route type: PICKUP or DELIVERY",
    )

    estimated_drive_time_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_drive_time_min: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Metrics (updated by planning engine) ─
    total_distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_duration_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_stops: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_volume_m3: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[RouteStatus] = mapped_column(
        Enum(RouteStatus, native_enum=False),
        nullable=False,
        default=RouteStatus.DRAFT,
        server_default=RouteStatus.DRAFT.value,
    )

    # Directions polyline for this driver's **current** ordered ``route_stops`` (e.g. depot → …).
    # Writer: planner or async job after initial build **or** after stop add/reorder/remove — call
    # your directions provider, then set all three fields together
    # (``navigation_fingerprint`` = ``compute_route_navigation_fingerprint`` in ``route_navigation``).
    navigation_encoded_polyline: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Provider blobs: e.g. polyline_format, computed_at, distance_m, duration_s, API version.
    navigation_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Must match live stop order; otherwise drive-mode GET omits the polyline and marks meta stale.
    navigation_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ── Relationships ────────────────────────
    plan = relationship("RoutePlan", lazy="raise", foreign_keys=[plan_id])
    driver = relationship("Driver", lazy="raise", foreign_keys=[driver_id])
    vehicle = relationship("Vehicle", lazy="raise", foreign_keys=[vehicle_id])

    def __repr__(self) -> str:
        return f"<Route plan={self.plan_id!r} driver={self.driver_id}>"


class RouteStop(BaseModelNoVersion):
    """An individual stop on a route, linked to a delivery stop.

    Uses BaseModelNoVersion — stops are always updated within the parent
    Route's transaction, protected by the Route's version column.

    Any change to ``sequence`` or to which stops exist on the parent ``Route`` should trigger a
    navigation refresh (recompute polyline + fingerprint on ``Route``) in the planner/job layer.
    """

    __tablename__ = "route_stops"

    route_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("routes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    delivery_stop_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("delivery_stops.id", ondelete="SET NULL"),
        nullable=True,
    )
    order_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc=(
            "Set for PICKUP route stops where the planner collects an order at its pickup_address. "
            "Mutually exclusive with delivery_stop_id in normal usage."
        ),
    )

    # ── Ordering ─────────────────────────────
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── Assignment ───────────────────────────
    assignment_source: Mapped[StopAssignmentSource] = mapped_column(
        Enum(StopAssignmentSource, native_enum=False),
        nullable=False,
        default=StopAssignmentSource.MANUAL,
        server_default=StopAssignmentSource.MANUAL.value,
    )

    # ── Metrics ──────────────────────────────
    estimated_arrival: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_arrival: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    distance_from_prev_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_from_prev_min: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[RouteStopStatus] = mapped_column(
        Enum(RouteStopStatus, native_enum=False),
        nullable=False,
        default=RouteStopStatus.PENDING,
        server_default=RouteStopStatus.PENDING.value,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    stop_flow_type: Mapped[RouteStopFlowType] = mapped_column(
        Enum(RouteStopFlowType, native_enum=False),
        nullable=False,
        default=RouteStopFlowType.DELIVERY,
        server_default=RouteStopFlowType.DELIVERY.value,
        index=True,
        doc="Pickup, delivery, or return leg for this stop (independent of ``routes.route_type``).",
    )

    # ── Driven history (per leg: previous stop / depot → this stop) ─────────
    # Written once when the route flips to COMPLETED by the route-history pipeline. The
    # raw GPS points in ``route_events`` (``LOCATION_PING``) are passed through OSRM ``/match``
    # to produce a compact encoded polyline string, which we store here as the auditable record.
    traveled_encoded_polyline: Mapped[str | None] = mapped_column(Text, nullable=True)
    traveled_distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traveled_duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traveled_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    traveled_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    traveled_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Relationships ────────────────────────
    route = relationship("Route", lazy="raise", foreign_keys=[route_id])

    def __repr__(self) -> str:
        return f"<RouteStop route={self.route_id} seq={self.sequence}>"


class RouteEvent(BaseModel):
    """Discrete telematics/safety events recorded for a route."""

    __tablename__ = "route_events"

    route_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("routes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    driver_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    route = relationship("Route", lazy="raise", foreign_keys=[route_id])

    def __repr__(self) -> str:
        return f"<RouteEvent route={self.route_id} type={self.event_type} at={self.occurred_at}>"


class StopPod(BaseModel):
    """Proof-of-delivery summary for a delivery stop."""

    __tablename__ = "stop_pod"
    __table_args__ = (UniqueConstraint("delivery_stop_id", name="uq_stop_pod_delivery_stop"),)

    delivery_stop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("delivery_stops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    photos_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    signature_image_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signature_required_snapshot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<StopPod stop={self.delivery_stop_id} photos={self.photos_count}>"


class StopPodPhoto(BaseModel):
    """Individual POD photo for a delivery stop."""

    __tablename__ = "stop_pod_photos"
    __table_args__ = (UniqueConstraint("delivery_stop_id", "sort_order", name="uq_stop_pod_photos_stop_order"),)

    delivery_stop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("delivery_stops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    image_key: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    uploaded_by_driver_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<StopPodPhoto stop={self.delivery_stop_id} key={self.image_key}>"


class PlanningAuditLog(AppendOnlyModel):
    """Append-only audit log for all planning decisions."""

    __tablename__ = "planning_audit_log"

    plan_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("route_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    action: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<PlanningAuditLog action={self.action}>"
