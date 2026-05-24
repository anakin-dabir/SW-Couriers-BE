"""Restore driver tables/columns incorrectly dropped by 80dc8ef71af4.

This is a corrective migration intended for environments where revision
80dc8ef71af4 (or subsequent revisions) removed driver-related tables/columns.
It is written to be idempotent: objects are created only if missing.

Revision ID: 9f1c2a3b4d5e
Revises: 8d03226dfb50
Create Date: 2026-03-18

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f1c2a3b4d5e"
down_revision: str | None = "8d03226dfb50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    try:
        cols = inspector.get_columns(table_name)
    except Exception:
        return False
    return any(c.get("name") == column_name for c in cols)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- Ensure enum TYPES exist (PostgreSQL) ---
    driver_document_kind_enum = sa.Enum(
        "DRIVING_LICENCE",
        "CPC_CERTIFICATE",
        "DIGITAL_TACHOGRAPH",
        "CUSTOM",
        name="driver_document_kind_enum",
    )
    traffic_violation_type_enum = sa.Enum(
        "SPEEDING",
        "RED_LIGHT",
        "PARKING",
        "BUS_LANE",
        name="traffic_violation_type_enum",
    )
    traffic_violation_status_enum = sa.Enum("UNPAID", "PAID", name="traffic_violation_status_enum")

    # checkfirst=True makes this safe if already created
    driver_document_kind_enum.create(bind, checkfirst=True)
    traffic_violation_type_enum.create(bind, checkfirst=True)
    traffic_violation_status_enum.create(bind, checkfirst=True)

    # --- Restore missing driver-related tables ---
    if not _has_table(inspector, "driver_documents"):
        op.create_table(
            "driver_documents",
            sa.Column("driver_id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("kind", driver_document_kind_enum, nullable=False),
            sa.Column("title", sa.String(length=255), nullable=True),
            sa.Column("file_key", sa.String(length=255), nullable=False),
            sa.Column("expiry_date", sa.Date(), nullable=True),
            sa.Column("content_type", sa.String(length=100), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=True),
            sa.Column("is_initial", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_driver_documents_driver_id"), "driver_documents", ["driver_id"], unique=False)

    if not _has_table(inspector, "driver_weekly_schedule"):
        op.create_table(
            "driver_weekly_schedule",
            sa.Column("driver_id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("day_of_week", sa.Integer(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("start_time", postgresql.TIME(), nullable=True),
            sa.Column("end_time", postgresql.TIME(), nullable=True),
            sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_driver_weekly_schedule_driver_id"), "driver_weekly_schedule", ["driver_id"], unique=False)

    if not _has_table(inspector, "driver_time_off"):
        op.create_table(
            "driver_time_off",
            sa.Column("driver_id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=False),
            sa.Column("type", sa.String(length=20), nullable=False, server_default=sa.text("'ANNUAL_LEAVE'")),
            sa.Column("days", sa.Integer(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_driver_time_off_driver_id"), "driver_time_off", ["driver_id"], unique=False)
    else:
        # If the table exists but was partially reverted, ensure newer columns are present.
        if not _has_column(inspector, "driver_time_off", "notes"):
            op.add_column("driver_time_off", sa.Column("notes", sa.Text(), nullable=True))
        if not _has_column(inspector, "driver_time_off", "is_paid"):
            op.add_column(
                "driver_time_off",
                sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            )

    if not _has_table(inspector, "driver_traffic_violations"):
        op.create_table(
            "driver_traffic_violations",
            sa.Column("driver_id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("violation_type", traffic_violation_type_enum, nullable=False),
            sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
            sa.Column("status", traffic_violation_status_enum, nullable=False, server_default=sa.text("'UNPAID'")),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("proof_key", sa.String(length=255), nullable=True),
            sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
            sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_driver_traffic_violations_driver_id"),
            "driver_traffic_violations",
            ["driver_id"],
            unique=False,
        )

    # --- Restore missing drivers columns (best-effort, safe for existing rows) ---
    if _has_table(inspector, "drivers"):
        missing_cols: list[tuple[str, sa.Column]] = []

        def add_missing(name: str, col: sa.Column) -> None:
            if not _has_column(inspector, "drivers", name):
                missing_cols.append((name, col))

        # NOTE: we keep these nullable or with server defaults to avoid failing on existing rows.
        add_missing("driver_code", sa.Column("driver_code", sa.String(length=20), nullable=True))
        add_missing("first_name", sa.Column("first_name", sa.String(length=100), nullable=True))
        add_missing("last_name", sa.Column("last_name", sa.String(length=100), nullable=True))
        add_missing("email", sa.Column("email", sa.String(length=255), nullable=True))
        add_missing("phone", sa.Column("phone", sa.String(length=50), nullable=True))
        add_missing("capacity", sa.Column("capacity", sa.String(length=20), nullable=True))
        add_missing("driver_type", sa.Column("driver_type", sa.String(length=20), nullable=True))
        add_missing("account_status", sa.Column("account_status", sa.String(length=30), nullable=True))
        add_missing("live_status", sa.Column("live_status", sa.String(length=30), nullable=True))
        add_missing("safety_score", sa.Column("safety_score", sa.Integer(), nullable=True))
        add_missing("on_time_deliveries", sa.Column("on_time_deliveries", sa.Integer(), nullable=True))
        add_missing("address_line1", sa.Column("address_line1", sa.String(length=255), nullable=True))
        add_missing("address_line2", sa.Column("address_line2", sa.String(length=255), nullable=True))
        add_missing("city", sa.Column("city", sa.String(length=100), nullable=True))
        add_missing("postcode", sa.Column("postcode", sa.String(length=20), nullable=True))
        add_missing("profile_photo_key", sa.Column("profile_photo_key", sa.String(length=255), nullable=True))

        for _, col in missing_cols:
            op.add_column("drivers", col)

        # Best-effort index for driver_code (skip if already exists)
        idx_names = {ix.get("name") for ix in inspector.get_indexes("drivers")}
        if "ix_drivers_driver_code" not in idx_names and _has_column(inspector, "drivers", "driver_code"):
            op.create_index(op.f("ix_drivers_driver_code"), "drivers", ["driver_code"], unique=True)


def downgrade() -> None:
    # Best-effort rollback for artifacts this migration may have added.
    #
    # Important: we intentionally DO NOT drop driver_* tables here, because older
    # downgrade steps (e.g. 8d03226dfb50) may still expect them to exist.
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "drivers"):
        return

    # Compatibility: older downgrades (e.g. f4670f5faf8d) may unconditionally drop
    # ix_drivers_driver_code. Ensure it exists so their drop doesn't fail.
    try:
        idx_names = {ix.get("name") for ix in inspector.get_indexes("drivers")}
    except Exception:
        idx_names = set()
    if "ix_drivers_driver_code" not in idx_names and _has_column(inspector, "drivers", "driver_code"):
        op.create_index("ix_drivers_driver_code", "drivers", ["driver_code"], unique=True)

    # Compatibility: f4670f5faf8d downgrade also unconditionally drops these columns.
    # If they don't exist in the current DB state, create them (nullable) so
    # later downgrades can drop them without failing.
    driver_cols_needed = {
        "driver_code": sa.Column("driver_code", sa.String(length=20), nullable=True),
        "first_name": sa.Column("first_name", sa.String(length=100), nullable=True),
        "last_name": sa.Column("last_name", sa.String(length=100), nullable=True),
        "email": sa.Column("email", sa.String(length=255), nullable=True),
        "phone": sa.Column("phone", sa.String(length=50), nullable=True),
        "capacity": sa.Column("capacity", sa.String(length=20), nullable=True),
        "driver_type": sa.Column("driver_type", sa.String(length=20), nullable=True),
        "account_status": sa.Column("account_status", sa.String(length=30), nullable=True),
        "live_status": sa.Column("live_status", sa.String(length=30), nullable=True),
        "safety_score": sa.Column("safety_score", sa.Integer(), nullable=True),
        "on_time_deliveries": sa.Column("on_time_deliveries", sa.Integer(), nullable=True),
        "address_line1": sa.Column("address_line1", sa.String(length=255), nullable=True),
        "address_line2": sa.Column("address_line2", sa.String(length=255), nullable=True),
        "city": sa.Column("city", sa.String(length=100), nullable=True),
        "postcode": sa.Column("postcode", sa.String(length=20), nullable=True),
        "profile_photo_key": sa.Column("profile_photo_key", sa.String(length=255), nullable=True),
    }

    for name, col in driver_cols_needed.items():
        if not _has_column(inspector, "drivers", name):
            op.add_column("drivers", col)
