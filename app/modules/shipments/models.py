"""Shipment event and delivery attempt models. Append-only audit trail.

Every state machine transition creates a ShipmentEvent record. These
are never updated or deleted — they form the complete package history.
"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import AppendOnlyModel


class ShipmentEvent(AppendOnlyModel):
    """Append-only shipment event. Created on every package status transition."""

    __tablename__ = "shipment_events"

    package_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Transition ───────────────────────────
    from_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)

    # ── Actor ────────────────────────────────
    triggered_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    triggered_by_role: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # ── Context ──────────────────────────────
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return f"<ShipmentEvent {self.from_status}→{self.to_status}>"


class DeliveryAttempt(AppendOnlyModel):
    """Record of each delivery attempt for a package."""

    __tablename__ = "delivery_attempts"

    package_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    delivery_stop_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("delivery_stops.id", ondelete="SET NULL"),
        nullable=True,
    )
    driver_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="SET NULL"),
        nullable=True,
    )

    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # POD for this attempt
    pod_photo_urls: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)

    def __repr__(self) -> str:
        return f"<DeliveryAttempt package={self.package_id} #{self.attempt_number}>"
