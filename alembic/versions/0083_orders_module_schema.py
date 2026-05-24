"""orders module schema: drop bookings, create orders + drafts, refit stops/packages.

Replaces the prior 0083_orders_consolidated and 0084_delivery_stop_return_actions
attempts. Server head is 0071; everything from 0072 onward is unapplied. This
migration runs after 0082_pickup_addresses (which already created the
pickup_addresses table and re-pointed bookings.pickup_address_id at it).


Revision ID: 0083_orders_module
Revises: 0082_pickup_addresses
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from alembic import op


revision: str = "0083_orders_module"
down_revision: str | None = "0082_pickup_addresses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_order_payment_method_enum = sa.Enum(
    "CARD_PAYMENT",
    "BANK_TRANSFER",
    "CREDIT_ACCOUNT",
    "CASH",
    name="order_payment_method_enum",
    native_enum=False,
)
_order_status_enum = sa.Enum(
    "PENDING_PICKUP",
    "PICKUP_SCHEDULED",
    "ENROUTE_PICKUP",
    "ENROUTE_WAREHOUSE",
    "AT_WAREHOUSE",
    "SORTING_IN_PROGRESS",
    "DELIVERY_IN_PROGRESS",
    "PARTIALLY_DELIVERED",
    "DELIVERED",
    "FAILED",
    "RETURN_IN_PROGRESS",
    "RETURN_IN_TRANSIT",
    "RETURNED",
    "CANCELLED",
    name="order_status_enum",
    native_enum=False,
)
_order_draft_status_enum = sa.Enum(
    "PENDING",
    "PUBLISHED",
    name="order_draft_status_enum",
    native_enum=False,
)
_delivery_stop_service_tier_enum = sa.Enum(
    "FASTEST",
    "STANDARD",
    "ECONOMY",
    name="delivery_stop_service_tier_enum",
    native_enum=False,
)
_delivery_stop_status_enum = sa.Enum(
    "PENDING_PICKUP",
    "PICKUP_SCHEDULED",
    "ENROUTE_PICKUP",
    "ENROUTE_WAREHOUSE",
    "AT_WAREHOUSE",
    "SORTING_IN_PROGRESS",
    "DELIVERY_SCHEDULED",
    "LOADED_FOR_DELIVERY",
    "OUT_FOR_DELIVERY",
    "DELIVERED",
    "PARTIALLY_DELIVERED",
    "DELIVERY_ATTEMPT_1_FAILED",
    "DELIVERY_ATTEMPT_2_FAILED",
    "DELIVERY_ATTEMPT_3_FAILED",
    "MIXED",
    "FAILED",
    "CANCELLED",
    "RETURN_INITIATED",
    "RETURN_IN_TRANSIT",
    "RETURNED",
    "DISPOSED",
    name="delivery_stop_status_enum",
    native_enum=False,
)
_package_status_enum = sa.Enum(
    "PENDING_PICKUP",
    "PICKUP_SCHEDULED",
    "ENROUTE_PICKUP",
    "ENROUTE_WAREHOUSE",
    "AT_WAREHOUSE",
    "SORTING_IN_PROGRESS",
    "DELIVERY_SCHEDULED",
    "LOADED_FOR_DELIVERY",
    "OUT_FOR_DELIVERY",
    "DELIVERED_TO_CUSTOMER",
    "CUSTOMER_NOT_HOME",
    "REFUSED_BY_CUSTOMER",
    "MISSING",
    "DAMAGED",
    "LEFT_AT_SAFE_PLACE",
    "RETURN_INITIATED",
    "RETURN_IN_TRANSIT",
    "RETURNED",
    "CANCELLED",
    "DISPOSED",
    name="package_status_enum",
    native_enum=False,
)
_return_resolution_enum = sa.Enum(
    "RETURN_TO_SENDER",
    "DISPOSE",
    name="delivery_stop_return_resolution_enum",
    native_enum=False,
)
_disposal_reason_enum = sa.Enum(
    "DAMAGED_PARCEL",
    "ABANDONED",
    "OPERATIONAL_INSTRUCTION",
    "OTHER",
    name="delivery_stop_disposal_reason_enum",
    native_enum=False,
)


def upgrade() -> None:
    op.execute("ALTER TABLE delivery_stops DROP CONSTRAINT IF EXISTS delivery_stops_booking_id_fkey")
    op.execute("ALTER TABLE delivery_stops DROP CONSTRAINT IF EXISTS delivery_stops_address_id_fkey")
    op.execute("ALTER TABLE packages DROP CONSTRAINT IF EXISTS packages_booking_id_fkey")
    op.execute("ALTER TABLE packages DROP CONSTRAINT IF EXISTS packages_warehouse_zone_id_fkey")
    op.execute("ALTER TABLE packages DROP CONSTRAINT IF EXISTS fk_packages_finalized_by_driver_id")
    op.execute("ALTER TABLE packages DROP CONSTRAINT IF EXISTS ck_packages_delivery_status_matches_status")
    op.execute("ALTER TABLE invoices DROP CONSTRAINT IF EXISTS invoices_booking_id_fkey")
    op.execute("ALTER TABLE shipment_events DROP CONSTRAINT IF EXISTS shipment_events_booking_id_fkey")
    op.execute("ALTER TABLE payment_risk_events DROP CONSTRAINT IF EXISTS payment_risk_events_booking_id_fkey")
    op.execute("ALTER TABLE bookings DROP CONSTRAINT IF EXISTS fk_bookings_pickup_address_id")

    op.execute("TRUNCATE TABLE delivery_stops, packages CASCADE")

    op.execute("DROP INDEX IF EXISTS ix_bookings_master_label_id")
    op.execute("DROP INDEX IF EXISTS ix_bookings_pickup_address_id")
    op.execute("DROP INDEX IF EXISTS ix_bookings_status")
    op.execute("DROP INDEX IF EXISTS ix_bookings_reference_number")
    op.execute("DROP INDEX IF EXISTS ix_bookings_organization_id")
    op.execute("DROP INDEX IF EXISTS ix_bookings_customer_id")
    op.drop_table("bookings")

    op.execute("CREATE SEQUENCE IF NOT EXISTS order_id_seq START WITH 1 INCREMENT BY 1")
    op.execute("CREATE SEQUENCE IF NOT EXISTS order_draft_id_seq START WITH 1 INCREMENT BY 1")
    op.execute("CREATE SEQUENCE IF NOT EXISTS master_label_id_seq START WITH 1 INCREMENT BY 1")
    op.execute("CREATE SEQUENCE IF NOT EXISTS delivery_stop_tracking_seq START WITH 1 INCREMENT BY 1")
    op.execute("CREATE SEQUENCE IF NOT EXISTS package_reference_seq START WITH 1 INCREMENT BY 1")

    op.create_table(
        "orders",
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column(
            "order_id",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'SWC-ORD-' || lpad(nextval('order_id_seq')::text, 6, '0')"),
        ),
        sa.Column(
            "master_label_id",
            sa.String(length=40),
            nullable=False,
            server_default=sa.text("'ML-' || lpad(nextval('master_label_id_seq')::text, 10, '0')"),
        ),
        sa.Column("organization_id", UUID(as_uuid=False), nullable=False),
        sa.Column("customer_id", UUID(as_uuid=False), nullable=False),
        sa.Column("created_by_id", UUID(as_uuid=False), nullable=True),
        sa.Column("pickup_address_id", UUID(as_uuid=False), nullable=True),
        sa.Column("subtotal", sa.Numeric(10, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("vat_amount", sa.Numeric(10, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("price_breakdown", JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payment_method", _order_payment_method_enum, nullable=True),
        sa.Column("payment_status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("payment_method_id", UUID(as_uuid=False), nullable=True),
        sa.Column("braintree_transaction_id", sa.String(length=100), nullable=True),
        sa.Column("status", _order_status_enum, nullable=False, server_default="PENDING_PICKUP"),
        sa.Column("tracking_token", sa.String(length=255), nullable=True),
        sa.Column("tracking_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_orders_organization_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["users.id"], name="fk_orders_customer_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name="orders_created_by_id_fkey", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["pickup_address_id"],
            ["pickup_addresses.id"],
            name="fk_orders_pickup_address_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["payment_method_id"],
            ["payment_methods.id"],
            name="orders_payment_method_id_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_orders_order_id"),
        sa.UniqueConstraint("master_label_id", name="uq_orders_master_label_id"),
        sa.UniqueConstraint("tracking_token", name="uq_orders_tracking_token"),
    )
    op.create_index("ix_orders_organization_id", "orders", ["organization_id"])
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])
    op.create_index("ix_orders_pickup_address_id", "orders", ["pickup_address_id"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_order_id", "orders", ["order_id"])
    op.create_index("ix_orders_master_label_id", "orders", ["master_label_id"])

    op.create_table(
        "order_drafts",
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column(
            "draft_id",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'DR-' || lpad(nextval('order_draft_id_seq')::text, 6, '0')"),
        ),
        sa.Column("organization_id", UUID(as_uuid=False), nullable=False),
        sa.Column("customer_id", UUID(as_uuid=False), nullable=False),
        sa.Column("created_by_id", UUID(as_uuid=False), nullable=True),
        sa.Column("status", _order_draft_status_enum, nullable=False, server_default="PENDING"),
        sa.Column("published_by_id", UUID(as_uuid=False), nullable=True),
        sa.Column(
            "payload",
            JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_order_drafts_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["users.id"], name="fk_order_drafts_customer_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name="order_drafts_created_by_id_fkey", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["published_by_id"],
            ["users.id"],
            name="order_drafts_published_by_id_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", name="uq_order_drafts_draft_id"),
    )
    op.create_index("ix_order_drafts_organization_id", "order_drafts", ["organization_id"])
    op.create_index("ix_order_drafts_customer_id", "order_drafts", ["customer_id"])
    op.create_index("ix_order_drafts_status", "order_drafts", ["status"])
    op.create_index("ix_order_drafts_draft_id", "order_drafts", ["draft_id"])

    op.execute("DROP INDEX IF EXISTS ix_delivery_stops_booking_id")
    op.alter_column("delivery_stops", "booking_id", new_column_name="order_id")
    op.create_foreign_key(
        "fk_delivery_stops_order_id",
        "delivery_stops",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_delivery_stops_order_id", "delivery_stops", ["order_id"])

    op.add_column("delivery_stops", sa.Column("recipient_first_name", sa.String(length=255), nullable=False))
    op.add_column("delivery_stops", sa.Column("recipient_last_name", sa.String(length=255), nullable=False))
    op.drop_column("delivery_stops", "recipient_name")

    op.alter_column(
        "delivery_stops",
        "recipient_phone",
        existing_type=sa.String(length=50),
        nullable=False,
    )
    op.alter_column(
        "delivery_stops",
        "recipient_email",
        existing_type=sa.String(length=255),
        nullable=False,
    )

    op.add_column("delivery_stops", sa.Column("line_1", sa.String(length=255), nullable=False))
    op.add_column("delivery_stops", sa.Column("line_2", sa.String(length=255), nullable=True))
    op.add_column("delivery_stops", sa.Column("city", sa.String(length=100), nullable=False))
    op.add_column("delivery_stops", sa.Column("postcode", sa.String(length=20), nullable=False))
    op.add_column("delivery_stops", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("delivery_stops", sa.Column("longitude", sa.Float(), nullable=True))

    op.add_column("delivery_stops", sa.Column("service_tier", _delivery_stop_service_tier_enum, nullable=True))
    op.add_column(
        "delivery_stops",
        sa.Column("signature_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "delivery_stops",
        sa.Column("safe_place_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("delivery_stops", "signature_required", server_default=None)
    op.alter_column("delivery_stops", "safe_place_allowed", server_default=None)

    op.add_column("delivery_stops", sa.Column("scheduled_for", sa.Date(), nullable=True))

    op.add_column("delivery_stops", sa.Column("return_initiated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "delivery_stops",
        sa.Column("return_initiated_by_id", UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "delivery_stops_return_initiated_by_id_fkey",
        "delivery_stops",
        "users",
        ["return_initiated_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("delivery_stops", sa.Column("return_resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "delivery_stops",
        sa.Column("return_resolved_by_id", UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "delivery_stops_return_resolved_by_id_fkey",
        "delivery_stops",
        "users",
        ["return_resolved_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("delivery_stops", sa.Column("return_resolution", _return_resolution_enum, nullable=True))
    op.add_column("delivery_stops", sa.Column("return_dispatch_date", sa.Date(), nullable=True))
    op.add_column("delivery_stops", sa.Column("return_cost", sa.Numeric(10, 2), nullable=True))
    op.add_column(
        "delivery_stops",
        sa.Column("return_cost_waived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("delivery_stops", "return_cost_waived", server_default=None)
    op.add_column("delivery_stops", sa.Column("return_notes", sa.Text(), nullable=True))
    op.add_column("delivery_stops", sa.Column("disposal_reason", _disposal_reason_enum, nullable=True))

    for col in (
        "address_id",
        "time_window_start",
        "time_window_end",
        "delivery_preference",
        "delivery_instructions",
        "sequence",
        "notes",
    ):
        op.drop_column("delivery_stops", col)

    op.alter_column(
        "delivery_stops",
        "status",
        existing_type=sa.String(length=30),
        type_=_delivery_stop_status_enum,
        existing_nullable=False,
        postgresql_using="status::text",
    )
    op.alter_column(
        "delivery_stops",
        "tracking_id",
        existing_type=sa.String(length=40),
        nullable=False,
        server_default=sa.text(
            "'TRK-' || lpad(nextval('delivery_stop_tracking_seq')::text, 8, '0')"
        ),
    )

    op.execute("DROP INDEX IF EXISTS ix_packages_booking_id")
    op.execute("DROP INDEX IF EXISTS ix_packages_tracking_id")
    op.execute("DROP INDEX IF EXISTS ix_packages_barcode_value")
    op.execute("DROP INDEX IF EXISTS ix_packages_finalized_by_driver_id")
    op.execute("DROP INDEX IF EXISTS ix_packages_delivery_status")

    op.alter_column("packages", "booking_id", new_column_name="order_id")
    op.create_foreign_key(
        "fk_packages_order_id",
        "packages",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_packages_order_id", "packages", ["order_id"])

    op.alter_column(
        "packages",
        "tracking_id",
        new_column_name="package_id",
        existing_type=sa.String(length=30),
        type_=sa.String(length=40),
        nullable=False,
        server_default=sa.text(
            "'PKG-' || lpad(nextval('package_reference_seq')::text, 8, '0')"
        ),
    )
    op.create_index("ix_packages_package_id", "packages", ["package_id"], unique=True)

    for col in (
        "description",
        "special_handling",
        "is_fragile",
        "requires_signature",
        "safe_place_allowed",
        "keep_upright",
        "damage_type",
        "damage_description",
        "damage_photo_urls",
        "damage_metadata",
        "warehouse_zone_id",
        "shelf_location",
        "notes",
        "attempt_count",
        "max_attempts",
        "barcode_value",
        "finalized_by_driver_id",
        "finalized_at",
        "finalization_reason_code",
        "finalization_notes",
        "delivery_status",
    ):
        op.drop_column("packages", col)

    op.add_column("packages", sa.Column("declared_weight_kg", sa.Float(), nullable=True))

    op.alter_column(
        "packages",
        "status",
        existing_type=sa.String(length=40),
        type_=_package_status_enum,
        existing_nullable=False,
        postgresql_using="status::text",
    )

    op.create_table(
        "delivery_stop_return_evidence_images",
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column("delivery_stop_id", UUID(as_uuid=False), nullable=False),
        sa.Column("image_key", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(
            ["delivery_stop_id"],
            ["delivery_stops.id"],
            name="fk_dse_evidence_delivery_stop_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_stop_id", "sort_order", name="uq_dse_evidence_stop_order"),
    )
    op.create_index(
        "ix_delivery_stop_return_evidence_images_delivery_stop_id",
        "delivery_stop_return_evidence_images",
        ["delivery_stop_id"],
    )

    op.alter_column("invoices", "booking_id", new_column_name="order_id")
    op.create_foreign_key(
        "fk_invoices_order_id",
        "invoices",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute("DROP INDEX IF EXISTS ix_shipment_events_booking_id")
    op.alter_column("shipment_events", "booking_id", new_column_name="order_id")
    op.create_foreign_key(
        "fk_shipment_events_order_id",
        "shipment_events",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_shipment_events_order_id", "shipment_events", ["order_id"])

    op.alter_column("payment_risk_events", "booking_id", new_column_name="order_id")
    op.create_foreign_key(
        "fk_payment_risk_events_order_id",
        "payment_risk_events",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_payment_risk_events_order_id", "payment_risk_events", ["order_id"])


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_payment_risk_events_order_id")
    op.execute("ALTER TABLE payment_risk_events DROP CONSTRAINT IF EXISTS fk_payment_risk_events_order_id")
    op.alter_column("payment_risk_events", "order_id", new_column_name="booking_id")

    op.execute("DROP INDEX IF EXISTS ix_shipment_events_order_id")
    op.execute("ALTER TABLE shipment_events DROP CONSTRAINT IF EXISTS fk_shipment_events_order_id")
    op.alter_column("shipment_events", "order_id", new_column_name="booking_id")

    op.execute("ALTER TABLE invoices DROP CONSTRAINT IF EXISTS fk_invoices_order_id")
    op.alter_column("invoices", "order_id", new_column_name="booking_id")

    op.execute("DROP INDEX IF EXISTS ix_delivery_stop_return_evidence_images_delivery_stop_id")
    op.drop_table("delivery_stop_return_evidence_images")

    op.alter_column(
        "packages",
        "status",
        existing_type=_package_status_enum,
        type_=sa.String(length=40),
        existing_nullable=False,
        postgresql_using="status::text",
    )
    op.drop_column("packages", "declared_weight_kg")

    op.add_column("packages", sa.Column("description", sa.String(length=255), nullable=True))
    op.add_column(
        "packages",
        sa.Column("special_handling", ARRAY(sa.String()), nullable=True),
    )
    op.add_column(
        "packages",
        sa.Column("is_fragile", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "packages",
        sa.Column("requires_signature", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "packages",
        sa.Column("safe_place_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "packages",
        sa.Column("keep_upright", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("packages", "is_fragile", server_default=None)
    op.alter_column("packages", "requires_signature", server_default=None)
    op.alter_column("packages", "safe_place_allowed", server_default=None)
    op.alter_column("packages", "keep_upright", server_default=None)
    op.add_column(
        "packages",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "packages",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
    )
    op.alter_column("packages", "attempt_count", server_default=None)
    op.alter_column("packages", "max_attempts", server_default=None)
    op.add_column("packages", sa.Column("damage_type", sa.String(length=50), nullable=True))
    op.add_column("packages", sa.Column("damage_description", sa.Text(), nullable=True))
    op.add_column(
        "packages",
        sa.Column("damage_photo_urls", ARRAY(sa.String()), nullable=True),
    )
    op.add_column(
        "packages",
        sa.Column("damage_metadata", JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("packages", sa.Column("warehouse_zone_id", UUID(as_uuid=False), nullable=True))
    op.create_foreign_key(
        "packages_warehouse_zone_id_fkey",
        "packages",
        "warehouse_zones",
        ["warehouse_zone_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("packages", sa.Column("shelf_location", sa.String(length=50), nullable=True))
    op.add_column("packages", sa.Column("notes", sa.Text(), nullable=True))

    op.add_column("packages", sa.Column("barcode_value", sa.String(length=80), nullable=True))
    op.create_index("ix_packages_barcode_value", "packages", ["barcode_value"], unique=True)
    op.add_column("packages", sa.Column("delivery_status", sa.String(length=40), nullable=True))
    op.create_index("ix_packages_delivery_status", "packages", ["delivery_status"])
    op.add_column("packages", sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("packages", sa.Column("finalized_by_driver_id", UUID(as_uuid=False), nullable=True))
    op.create_index("ix_packages_finalized_by_driver_id", "packages", ["finalized_by_driver_id"])
    op.create_foreign_key(
        "fk_packages_finalized_by_driver_id",
        "packages",
        "drivers",
        ["finalized_by_driver_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("packages", sa.Column("finalization_reason_code", sa.String(length=80), nullable=True))
    op.add_column("packages", sa.Column("finalization_notes", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_packages_delivery_status_matches_status",
        "packages",
        "("
        "(status IN ('DELIVERED_TO_CUSTOMER','LEFT_AT_SAFE_PLACE','CUSTOMER_NOT_HOME','REFUSED_BY_CUSTOMER','MISSING') "
        "AND delivery_status = status)"
        " OR "
        "(status NOT IN ('DELIVERED_TO_CUSTOMER','LEFT_AT_SAFE_PLACE','CUSTOMER_NOT_HOME','REFUSED_BY_CUSTOMER','MISSING') "
        "AND delivery_status IS NULL)"
        ")",
    )

    op.execute("ALTER TABLE packages ALTER COLUMN package_id DROP DEFAULT")
    op.drop_index("ix_packages_package_id", table_name="packages")
    op.alter_column(
        "packages",
        "package_id",
        new_column_name="tracking_id",
        existing_type=sa.String(length=40),
        type_=sa.String(length=30),
        existing_nullable=False,
        nullable=True,
    )
    op.create_index("ix_packages_tracking_id", "packages", ["tracking_id"], unique=True)

    op.drop_index("ix_packages_order_id", table_name="packages")
    op.execute("ALTER TABLE packages DROP CONSTRAINT IF EXISTS fk_packages_order_id")
    op.alter_column("packages", "order_id", new_column_name="booking_id")

    op.alter_column(
        "delivery_stops",
        "status",
        existing_type=_delivery_stop_status_enum,
        type_=sa.String(length=30),
        existing_nullable=False,
        postgresql_using="status::text",
    )
    op.alter_column(
        "delivery_stops",
        "tracking_id",
        existing_type=sa.String(length=40),
        server_default=None,
    )

    op.drop_column("delivery_stops", "disposal_reason")
    op.drop_column("delivery_stops", "return_notes")
    op.drop_column("delivery_stops", "return_cost_waived")
    op.drop_column("delivery_stops", "return_cost")
    op.drop_column("delivery_stops", "return_dispatch_date")
    op.drop_column("delivery_stops", "return_resolution")
    op.execute(
        "ALTER TABLE delivery_stops DROP CONSTRAINT IF EXISTS delivery_stops_return_resolved_by_id_fkey"
    )
    op.drop_column("delivery_stops", "return_resolved_by_id")
    op.drop_column("delivery_stops", "return_resolved_at")
    op.execute(
        "ALTER TABLE delivery_stops DROP CONSTRAINT IF EXISTS delivery_stops_return_initiated_by_id_fkey"
    )
    op.drop_column("delivery_stops", "return_initiated_by_id")
    op.drop_column("delivery_stops", "return_initiated_at")
    op.drop_column("delivery_stops", "scheduled_for")
    op.drop_column("delivery_stops", "safe_place_allowed")
    op.drop_column("delivery_stops", "signature_required")
    op.drop_column("delivery_stops", "service_tier")
    op.drop_column("delivery_stops", "longitude")
    op.drop_column("delivery_stops", "latitude")
    op.drop_column("delivery_stops", "postcode")
    op.drop_column("delivery_stops", "city")
    op.drop_column("delivery_stops", "line_2")
    op.drop_column("delivery_stops", "line_1")

    op.alter_column(
        "delivery_stops",
        "recipient_phone",
        existing_type=sa.String(length=50),
        nullable=True,
    )
    op.alter_column(
        "delivery_stops",
        "recipient_email",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.add_column(
        "delivery_stops",
        sa.Column("recipient_name", sa.String(length=255), nullable=False, server_default=""),
    )
    op.alter_column("delivery_stops", "recipient_name", server_default=None)
    op.drop_column("delivery_stops", "recipient_last_name")
    op.drop_column("delivery_stops", "recipient_first_name")

    op.add_column("delivery_stops", sa.Column("address_id", UUID(as_uuid=False), nullable=True))
    op.create_foreign_key(
        "delivery_stops_address_id_fkey",
        "delivery_stops",
        "addresses",
        ["address_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "delivery_stops",
        sa.Column("time_window_start", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "delivery_stops",
        sa.Column("time_window_end", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "delivery_stops",
        sa.Column("delivery_preference", sa.String(length=30), nullable=True),
    )
    op.add_column("delivery_stops", sa.Column("delivery_instructions", sa.Text(), nullable=True))
    op.add_column("delivery_stops", sa.Column("sequence", sa.Integer(), nullable=True))
    op.add_column("delivery_stops", sa.Column("notes", sa.Text(), nullable=True))

    op.drop_index("ix_delivery_stops_order_id", table_name="delivery_stops")
    op.execute(
        "ALTER TABLE delivery_stops DROP CONSTRAINT IF EXISTS fk_delivery_stops_order_id"
    )
    op.alter_column("delivery_stops", "order_id", new_column_name="booking_id")

    op.drop_index("ix_order_drafts_draft_id", table_name="order_drafts")
    op.drop_index("ix_order_drafts_status", table_name="order_drafts")
    op.drop_index("ix_order_drafts_customer_id", table_name="order_drafts")
    op.drop_index("ix_order_drafts_organization_id", table_name="order_drafts")
    op.drop_table("order_drafts")

    op.drop_index("ix_orders_master_label_id", table_name="orders")
    op.drop_index("ix_orders_order_id", table_name="orders")
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_pickup_address_id", table_name="orders")
    op.drop_index("ix_orders_customer_id", table_name="orders")
    op.drop_index("ix_orders_organization_id", table_name="orders")
    op.drop_table("orders")

    op.execute("DROP SEQUENCE IF EXISTS package_reference_seq")
    op.execute("DROP SEQUENCE IF EXISTS delivery_stop_tracking_seq")
    op.execute("DROP SEQUENCE IF EXISTS master_label_id_seq")
    op.execute("DROP SEQUENCE IF EXISTS order_draft_id_seq")
    op.execute("DROP SEQUENCE IF EXISTS order_id_seq")

    op.create_table(
        "bookings",
        sa.Column("reference_number", sa.String(length=30), nullable=False),
        sa.Column("customer_id", UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", UUID(as_uuid=False), nullable=True),
        sa.Column("contact_name", sa.String(length=255), nullable=False),
        sa.Column("contact_email", sa.String(length=255), nullable=False),
        sa.Column("contact_phone", sa.String(length=50), nullable=False),
        sa.Column("pickup_address_id", UUID(as_uuid=False), nullable=True),
        sa.Column("pickup_instructions", sa.Text(), nullable=True),
        sa.Column("service_tier", sa.String(length=30), nullable=False),
        sa.Column("special_instructions", sa.Text(), nullable=True),
        sa.Column("subtotal", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("vat_amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            "price_breakdown",
            JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("payment_status", sa.String(length=30), nullable=False),
        sa.Column("payment_method_id", UUID(as_uuid=False), nullable=True),
        sa.Column("braintree_transaction_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("tracking_token", sa.String(length=255), nullable=True),
        sa.Column("tracking_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("master_label_id", sa.String(length=40), nullable=True),
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["payment_method_id"], ["payment_methods.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["pickup_address_id"],
            ["pickup_addresses.id"],
            name="fk_bookings_pickup_address_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tracking_token"),
    )
    op.create_index("ix_bookings_customer_id", "bookings", ["customer_id"])
    op.create_index("ix_bookings_organization_id", "bookings", ["organization_id"])
    op.create_index("ix_bookings_reference_number", "bookings", ["reference_number"], unique=True)
    op.create_index("ix_bookings_status", "bookings", ["status"])
    op.create_index("ix_bookings_master_label_id", "bookings", ["master_label_id"], unique=True)
    op.create_index("ix_bookings_pickup_address_id", "bookings", ["pickup_address_id"])

    op.create_foreign_key(
        "delivery_stops_booking_id_fkey",
        "delivery_stops",
        "bookings",
        ["booking_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_delivery_stops_booking_id", "delivery_stops", ["booking_id"])

    op.create_foreign_key(
        "packages_booking_id_fkey",
        "packages",
        "bookings",
        ["booking_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_packages_booking_id", "packages", ["booking_id"])

    op.create_foreign_key(
        "invoices_booking_id_fkey",
        "invoices",
        "bookings",
        ["booking_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_foreign_key(
        "shipment_events_booking_id_fkey",
        "shipment_events",
        "bookings",
        ["booking_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_shipment_events_booking_id", "shipment_events", ["booking_id"])

    op.create_foreign_key(
        "payment_risk_events_booking_id_fkey",
        "payment_risk_events",
        "bookings",
        ["booking_id"],
        ["id"],
        ondelete="SET NULL",
    )
