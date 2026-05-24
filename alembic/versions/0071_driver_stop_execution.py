"""driver_stop_execution

Revision ID: 0071_driver_stop_exec
Revises: 0070_driver_self_consents
Create Date: 2026-04-09 23:15:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0071_driver_stop_exec"
down_revision: Union[str, None] = "0070_driver_self_consents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stop_notes",
        sa.Column("delivery_stop_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("note_type", sa.String(length=30), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_blocking", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["delivery_stop_id"], ["delivery_stops.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stop_notes_delivery_stop_id", "stop_notes", ["delivery_stop_id"], unique=False)
    op.create_index("ix_stop_notes_note_type", "stop_notes", ["note_type"], unique=False)

    op.create_table(
        "stop_note_images",
        sa.Column("stop_note_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("image_key", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["stop_note_id"], ["stop_notes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stop_note_id", "sort_order", name="uq_stop_note_images_note_order"),
    )
    op.create_index("ix_stop_note_images_stop_note_id", "stop_note_images", ["stop_note_id"], unique=False)

    op.create_table(
        "stop_note_acknowledgements",
        sa.Column("delivery_stop_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("notes_hash", sa.String(length=64), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["delivery_stop_id"], ["delivery_stops.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "delivery_stop_id",
            "driver_id",
            "notes_hash",
            name="uq_stop_note_ack_stop_driver_hash",
        ),
    )
    op.create_index("ix_stop_note_acknowledgements_delivery_stop_id", "stop_note_acknowledgements", ["delivery_stop_id"], unique=False)
    op.create_index("ix_stop_note_acknowledgements_driver_id", "stop_note_acknowledgements", ["driver_id"], unique=False)
    op.create_index("ix_stop_note_acknowledgements_notes_hash", "stop_note_acknowledgements", ["notes_hash"], unique=False)

    op.create_table(
        "package_scan_logs",
        sa.Column("route_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("route_stop_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("delivery_stop_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("driver_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("scan_value", sa.String(length=120), nullable=False),
        sa.Column("result", sa.String(length=40), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["delivery_stop_id"], ["delivery_stops.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["package_id"], ["packages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["route_stop_id"], ["route_stops.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_package_scan_logs_delivery_stop_id", "package_scan_logs", ["delivery_stop_id"], unique=False)
    op.create_index("ix_package_scan_logs_driver_id", "package_scan_logs", ["driver_id"], unique=False)
    op.create_index("ix_package_scan_logs_package_id", "package_scan_logs", ["package_id"], unique=False)
    op.create_index("ix_package_scan_logs_result", "package_scan_logs", ["result"], unique=False)
    op.create_index("ix_package_scan_logs_route_id", "package_scan_logs", ["route_id"], unique=False)
    op.create_index("ix_package_scan_logs_route_stop_id", "package_scan_logs", ["route_stop_id"], unique=False)

    op.create_table(
        "package_missing_reports",
        sa.Column("package_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("route_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("route_stop_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("delivery_stop_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["delivery_stop_id"], ["delivery_stops.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["package_id"], ["packages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["route_stop_id"], ["route_stops.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_package_missing_reports_delivery_stop_id", "package_missing_reports", ["delivery_stop_id"], unique=False)
    op.create_index("ix_package_missing_reports_driver_id", "package_missing_reports", ["driver_id"], unique=False)
    op.create_index("ix_package_missing_reports_package_id", "package_missing_reports", ["package_id"], unique=False)
    op.create_index("ix_package_missing_reports_route_id", "package_missing_reports", ["route_id"], unique=False)
    op.create_index("ix_package_missing_reports_route_stop_id", "package_missing_reports", ["route_stop_id"], unique=False)

    op.create_table(
        "stop_pod",
        sa.Column("delivery_stop_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("photos_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("signature_image_key", sa.String(length=255), nullable=True),
        sa.Column("signature_required_snapshot", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["delivery_stop_id"], ["delivery_stops.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_stop_id", name="uq_stop_pod_delivery_stop"),
    )
    op.create_index("ix_stop_pod_delivery_stop_id", "stop_pod", ["delivery_stop_id"], unique=False)

    op.create_table(
        "stop_pod_photos",
        sa.Column("delivery_stop_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("image_key", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("uploaded_by_driver_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["delivery_stop_id"], ["delivery_stops.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_driver_id"], ["drivers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_stop_id", "sort_order", name="uq_stop_pod_photos_stop_order"),
    )
    op.create_index("ix_stop_pod_photos_delivery_stop_id", "stop_pod_photos", ["delivery_stop_id"], unique=False)
    op.create_index("ix_stop_pod_photos_uploaded_by_driver_id", "stop_pod_photos", ["uploaded_by_driver_id"], unique=False)

    op.add_column("packages", sa.Column("barcode_value", sa.String(length=80), nullable=True))
    op.create_index("ix_packages_barcode_value", "packages", ["barcode_value"], unique=True)
    op.add_column("packages", sa.Column("delivery_status", sa.String(length=40), nullable=True))
    op.create_index("ix_packages_delivery_status", "packages", ["delivery_status"], unique=False)
    op.add_column("packages", sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("packages", sa.Column("finalized_by_driver_id", postgresql.UUID(as_uuid=False), nullable=True))
    op.create_index("ix_packages_finalized_by_driver_id", "packages", ["finalized_by_driver_id"], unique=False)
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


def downgrade() -> None:
    op.execute("ALTER TABLE packages DROP CONSTRAINT IF EXISTS ck_packages_delivery_status_matches_status")
    op.drop_column("packages", "finalization_notes")
    op.drop_column("packages", "finalization_reason_code")
    op.drop_constraint("fk_packages_finalized_by_driver_id", "packages", type_="foreignkey")
    op.drop_index("ix_packages_finalized_by_driver_id", table_name="packages")
    op.drop_column("packages", "finalized_by_driver_id")
    op.drop_column("packages", "finalized_at")
    op.drop_index("ix_packages_delivery_status", table_name="packages")
    op.drop_column("packages", "delivery_status")
    op.drop_index("ix_packages_barcode_value", table_name="packages")
    op.drop_column("packages", "barcode_value")

    op.drop_index("ix_stop_pod_photos_uploaded_by_driver_id", table_name="stop_pod_photos")
    op.drop_index("ix_stop_pod_photos_delivery_stop_id", table_name="stop_pod_photos")
    op.drop_table("stop_pod_photos")
    op.drop_index("ix_stop_pod_delivery_stop_id", table_name="stop_pod")
    op.drop_table("stop_pod")

    op.drop_index("ix_package_missing_reports_route_stop_id", table_name="package_missing_reports")
    op.drop_index("ix_package_missing_reports_route_id", table_name="package_missing_reports")
    op.drop_index("ix_package_missing_reports_package_id", table_name="package_missing_reports")
    op.drop_index("ix_package_missing_reports_driver_id", table_name="package_missing_reports")
    op.drop_index("ix_package_missing_reports_delivery_stop_id", table_name="package_missing_reports")
    op.drop_table("package_missing_reports")

    op.drop_index("ix_package_scan_logs_route_stop_id", table_name="package_scan_logs")
    op.drop_index("ix_package_scan_logs_route_id", table_name="package_scan_logs")
    op.drop_index("ix_package_scan_logs_result", table_name="package_scan_logs")
    op.drop_index("ix_package_scan_logs_package_id", table_name="package_scan_logs")
    op.drop_index("ix_package_scan_logs_driver_id", table_name="package_scan_logs")
    op.drop_index("ix_package_scan_logs_delivery_stop_id", table_name="package_scan_logs")
    op.drop_table("package_scan_logs")

    op.drop_index("ix_stop_note_acknowledgements_notes_hash", table_name="stop_note_acknowledgements")
    op.drop_index("ix_stop_note_acknowledgements_driver_id", table_name="stop_note_acknowledgements")
    op.drop_index("ix_stop_note_acknowledgements_delivery_stop_id", table_name="stop_note_acknowledgements")
    op.drop_table("stop_note_acknowledgements")

    op.drop_index("ix_stop_note_images_stop_note_id", table_name="stop_note_images")
    op.drop_table("stop_note_images")

    op.drop_index("ix_stop_notes_note_type", table_name="stop_notes")
    op.drop_index("ix_stop_notes_delivery_stop_id", table_name="stop_notes")
    op.drop_table("stop_notes")
