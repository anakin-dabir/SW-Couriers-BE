from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import Sequence

from app.common.enums.sequence import SequentialPrefix
from app.common.models import AppendOnlyModel, Base, BaseModel
from app.modules.orders.enums import (
    DeliveryStopStatus,
    DisposalReason,
    OrderDraftStatus,
    OrderStatus,
    PackageStatus,
    ReturnResolution,
)
from app.modules.organizations.enums import PaymentModel

order_id_seq = Sequence("order_id_seq", metadata=Base.metadata)
order_draft_id_seq = Sequence("order_draft_id_seq", metadata=Base.metadata)
master_label_id_seq = Sequence("master_label_id_seq", metadata=Base.metadata)
delivery_stop_tracking_seq = Sequence("delivery_stop_tracking_seq", metadata=Base.metadata)
package_reference_seq = Sequence("package_reference_seq", metadata=Base.metadata)


class OrderDraft(BaseModel):
    __tablename__ = "order_drafts"

    draft_id: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'{SequentialPrefix.DRAFT}-' || lpad(nextval('{order_draft_id_seq.name}')::text, 6, '0')"),
    )

    organization_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    customer_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[OrderDraftStatus] = mapped_column(
        Enum(OrderDraftStatus, name="order_draft_status_enum", native_enum=False),
        nullable=False,
        default=OrderDraftStatus.PENDING,
        index=True,
    )
    published_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="order_drafts_published_by_id_fkey", ondelete="SET NULL"),
        nullable=True,
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    organization = relationship("Organization", lazy="raise", foreign_keys=[organization_id])
    customer = relationship("User", lazy="raise", foreign_keys=[customer_id])
    created_by = relationship("User", lazy="raise", foreign_keys=[created_by_id])
    published_by = relationship("User", lazy="raise", foreign_keys=[published_by_id])

    def __repr__(self) -> str:
        return f"<OrderDraft {self.draft_id}>"


class Order(BaseModel):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'{SequentialPrefix.ORDER}-' || lpad(nextval('{order_id_seq.name}')::text, 6, '0')"),
    )
    master_label_id: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'{SequentialPrefix.MASTER_LABEL}-' || lpad(nextval('{master_label_id_seq.name}')::text, 10, '0')"),
    )

    organization_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    contact_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_orders_contact_user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    pickup_address_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("pickup_addresses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    requested_pickup_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)

    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    price_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    pricing_config_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    payment_method: Mapped[PaymentModel | None] = mapped_column(
        String(30),
        nullable=True,
    )
    payment_method_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("org_payment_methods.id", name="fk_orders_org_payment_method_id", ondelete="SET NULL"),
        nullable=True,
    )
    braintree_transaction_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status_enum", native_enum=False),
        nullable=False,
        default=OrderStatus.PENDING_PICKUP,
        index=True,
    )

    tracking_token: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    tracking_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer = relationship("User", lazy="raise", foreign_keys=[customer_id])
    contact_user = relationship("User", lazy="raise", foreign_keys=[contact_user_id])
    created_by = relationship("User", lazy="raise", foreign_keys=[created_by_id])
    organization = relationship("Organization", lazy="raise", foreign_keys=[organization_id])
    pickup_address = relationship("PickupAddress", lazy="raise", foreign_keys=[pickup_address_id])

    def __repr__(self) -> str:
        return f"<Order {self.order_id} status={self.status}>"


class DeliveryStop(BaseModel):
    __tablename__ = "delivery_stops"

    order_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tracking_id: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'{SequentialPrefix.STOP_TRACKING}-' || lpad(nextval('{delivery_stop_tracking_seq.name}')::text, 8, '0')"),
    )

    recipient_first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_last_name: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_phone: Mapped[str] = mapped_column(String(50), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)

    line_1: Mapped[str] = mapped_column(String(255), nullable=False)
    line_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    postcode: Mapped[str] = mapped_column(String(20), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    service_tier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    service_tier_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("service_tier.id", name="fk_delivery_stops_service_tier_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    price_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    signature_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    safe_place_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[DeliveryStopStatus] = mapped_column(
        Enum(DeliveryStopStatus, name="delivery_stop_status_enum", native_enum=False),
        nullable=False,
        default=DeliveryStopStatus.PENDING_PICKUP,
    )

    scheduled_for: Mapped[date | None] = mapped_column(Date, nullable=True)

    return_initiated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    return_initiated_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="delivery_stops_return_initiated_by_id_fkey", ondelete="SET NULL"),
        nullable=True,
    )
    return_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    return_resolved_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="delivery_stops_return_resolved_by_id_fkey", ondelete="SET NULL"),
        nullable=True,
    )
    return_resolution: Mapped[ReturnResolution | None] = mapped_column(
        Enum(ReturnResolution, name="delivery_stop_return_resolution_enum", native_enum=False),
        nullable=True,
    )
    return_dispatch_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    return_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    return_cost_waived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    return_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    disposal_reason: Mapped[DisposalReason | None] = mapped_column(
        Enum(DisposalReason, name="delivery_stop_disposal_reason_enum", native_enum=False),
        nullable=True,
    )

    order = relationship("Order", lazy="raise", foreign_keys=[order_id])

    def __repr__(self) -> str:
        return f"<DeliveryStop order={self.order_id} recipient={self.recipient_first_name}>"


class DeliveryStopReturnEvidenceImage(BaseModel):
    __tablename__ = "delivery_stop_return_evidence_images"
    __table_args__ = (
        UniqueConstraint("delivery_stop_id", "sort_order", name="uq_dse_evidence_stop_order"),
    )

    delivery_stop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("delivery_stops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    image_key: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    delivery_stop = relationship("DeliveryStop", lazy="raise", foreign_keys=[delivery_stop_id])

    def __repr__(self) -> str:
        return f"<DeliveryStopReturnEvidenceImage stop={self.delivery_stop_id} key={self.image_key}>"


class DeliveryStopFailedAttempt(BaseModel):
    __tablename__ = "delivery_stop_failed_attempts"
    __table_args__ = (
        UniqueConstraint(
            "delivery_stop_id", "attempt_number", name="uq_dsfa_stop_attempt_number"
        ),
    )

    delivery_stop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("delivery_stops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    driver_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True
    )
    vehicle_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True
    )
    route_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("routes.id", ondelete="SET NULL"),
        nullable=True,
    )
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    delivery_stop = relationship("DeliveryStop", lazy="raise", foreign_keys=[delivery_stop_id])

    def __repr__(self) -> str:
        return (
            f"<DeliveryStopFailedAttempt stop={self.delivery_stop_id} "
            f"n={self.attempt_number} reason={self.failure_reason}>"
        )


class DeliveryStopReturnAttempt(BaseModel):
    __tablename__ = "delivery_stop_return_attempts"
    __table_args__ = (
        UniqueConstraint(
            "delivery_stop_id", "attempt_number", name="uq_dsra_stop_attempt_number"
        ),
    )

    delivery_stop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("delivery_stops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    driver_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True
    )
    vehicle_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True
    )
    route_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("routes.id", ondelete="SET NULL"),
        nullable=True,
    )
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    delivery_stop = relationship("DeliveryStop", lazy="raise", foreign_keys=[delivery_stop_id])

    def __repr__(self) -> str:
        return (
            f"<DeliveryStopReturnAttempt stop={self.delivery_stop_id} "
            f"n={self.attempt_number} reason={self.failure_reason}>"
        )


class StopNote(BaseModel):
    __tablename__ = "stop_notes"

    delivery_stop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("delivery_stops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    note_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    package_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    delivery_stop = relationship("DeliveryStop", lazy="raise", foreign_keys=[delivery_stop_id])

    def __repr__(self) -> str:
        return f"<StopNote stop={self.delivery_stop_id} type={self.note_type}>"


class StopNoteImage(BaseModel):
    __tablename__ = "stop_note_images"
    __table_args__ = (UniqueConstraint("stop_note_id", "sort_order", name="uq_stop_note_images_note_order"),)

    stop_note_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("stop_notes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    image_key: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    stop_note = relationship("StopNote", lazy="raise", foreign_keys=[stop_note_id])

    def __repr__(self) -> str:
        return f"<StopNoteImage note={self.stop_note_id} key={self.image_key}>"


class StopNoteAcknowledgement(BaseModel):
    __tablename__ = "stop_note_acknowledgements"
    __table_args__ = (
        UniqueConstraint(
            "delivery_stop_id",
            "driver_id",
            "notes_hash",
            name="uq_stop_note_ack_stop_driver_hash",
        ),
    )

    delivery_stop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("delivery_stops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    driver_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notes_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    delivery_stop = relationship("DeliveryStop", lazy="raise", foreign_keys=[delivery_stop_id])
    driver = relationship("Driver", lazy="raise", foreign_keys=[driver_id])

    def __repr__(self) -> str:
        return f"<StopNoteAcknowledgement stop={self.delivery_stop_id} driver={self.driver_id}>"


class Package(BaseModel):
    __tablename__ = "packages"

    package_id: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(f"'{SequentialPrefix.PACKAGE_REFERENCE}-' || lpad(nextval('{package_reference_seq.name}')::text, 8, '0')"),
    )
    order_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    delivery_stop_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("delivery_stops.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    length_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    width_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    declared_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    declared_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    status: Mapped[PackageStatus] = mapped_column(
        Enum(PackageStatus, name="package_status_enum", native_enum=False),
        nullable=False,
        default=PackageStatus.PENDING_PICKUP,
        index=True,
    )
    is_damaged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    price_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    order = relationship("Order", lazy="raise", foreign_keys=[order_id])

    def __repr__(self) -> str:
        return f"<Package {self.package_id} status={self.status}>"


class OrderEvent(AppendOnlyModel):
    __tablename__ = "order_events"
    __table_args__ = (Index("ix_order_events_order_id_created_at", "order_id", "created_at"),)

    order_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("orders.id", name="fk_order_events_order_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_status: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_order_events_actor_user_id", ondelete="SET NULL"),
        nullable=True,
    )

    order = relationship("Order", lazy="raise", foreign_keys=[order_id])


class DeliveryStopEvent(AppendOnlyModel):
    __tablename__ = "delivery_stop_events"
    __table_args__ = (Index("ix_delivery_stop_events_stop_id_created_at", "delivery_stop_id", "created_at"),)

    delivery_stop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("delivery_stops.id", name="fk_delivery_stop_events_stop_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_status: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_delivery_stop_events_actor_user_id", ondelete="SET NULL"),
        nullable=True,
    )

    delivery_stop = relationship("DeliveryStop", lazy="raise", foreign_keys=[delivery_stop_id])


class PackageEvent(AppendOnlyModel):
    __tablename__ = "package_events"
    __table_args__ = (Index("ix_package_events_package_id_created_at", "package_id", "created_at"),)

    package_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("packages.id", name="fk_package_events_package_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_status: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="fk_package_events_actor_user_id", ondelete="SET NULL"),
        nullable=True,
    )

    package = relationship("Package", lazy="raise", foreign_keys=[package_id])


class PackageScanLog(BaseModel):
    __tablename__ = "package_scan_logs"

    route_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("routes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    route_stop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("route_stops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    delivery_stop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("delivery_stops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    package_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("packages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    driver_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scan_value: Mapped[str] = mapped_column(String(120), nullable=False)
    result: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<PackageScanLog route_stop={self.route_stop_id} result={self.result}>"


class PackageMissingReport(BaseModel):
    __tablename__ = "package_missing_reports"

    package_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    route_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("routes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    route_stop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("route_stops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    delivery_stop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("delivery_stops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    driver_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("drivers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<PackageMissingReport package={self.package_id} reason={self.reason_code}>"
