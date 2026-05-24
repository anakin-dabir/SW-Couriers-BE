"""vehicle_inspections ORM alignment + vehicle_defects.inspection_id FK.

Revision ID: 0068_vi_defect_fk
Revises: 0067_status_reason

Canonical migration for current VehicleInspection / VehicleDefect models:

- vehicle_inspections: create if missing (full ORM shape); else upgrade legacy
  da815 shape (status, nullable result, drop photo_urls, geo, declaration,
  signature, indexes).
- vehicle_defects: add nullable inspection_id -> vehicle_inspections.id
  (ON DELETE SET NULL) when missing.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from alembic import op

revision = "0068_vi_defect_fk"
down_revision = "0067_status_reason"
branch_labels = None
depends_on = None

_FULL_CREATE_MARKER = "alembic_0068_full_create"


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)

    if not insp.has_table("vehicle_inspections"):
        op.create_table(
            "vehicle_inspections",
            sa.Column("id", UUID(as_uuid=False), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("version", sa.Integer(), server_default="1", nullable=False),
            sa.Column("vehicle_id", UUID(as_uuid=False), nullable=False),
            sa.Column("driver_id", UUID(as_uuid=False), nullable=False),
            sa.Column("inspection_type", sa.String(length=30), nullable=False, server_default="PRE_TRIP"),
            sa.Column("result", sa.String(length=10), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="IN_PROGRESS"),
            sa.Column("mileage", sa.Float(), nullable=True),
            sa.Column("checklist", JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("latitude", sa.Float(), nullable=True),
            sa.Column("longitude", sa.Float(), nullable=True),
            sa.Column("ip_address", sa.String(length=45), nullable=True),
            sa.Column("declaration_accepted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("signature_path", sa.String(length=500), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_vehicle_inspections_vehicle_id", "vehicle_inspections", ["vehicle_id"], unique=False)
        op.create_index("ix_vehicle_inspections_driver_id", "vehicle_inspections", ["driver_id"], unique=False)
        op.create_index("ix_vehicle_inspections_status", "vehicle_inspections", ["status"], unique=False)
        op.create_index(
            "ix_vehicle_inspections_vehicle_driver",
            "vehicle_inspections",
            ["vehicle_id", "driver_id"],
            unique=False,
        )
        op.create_index("ix_vehicle_inspections_created_at", "vehicle_inspections", ["created_at"], unique=False)
        op.execute(sa.text("ALTER TABLE vehicle_inspections ALTER COLUMN inspection_type DROP DEFAULT"))
        op.execute(sa.text("ALTER TABLE vehicle_inspections ALTER COLUMN status DROP DEFAULT"))
        op.execute(sa.text("ALTER TABLE vehicle_inspections ALTER COLUMN declaration_accepted DROP DEFAULT"))
        op.execute(sa.text(f"COMMENT ON TABLE vehicle_inspections IS '{_FULL_CREATE_MARKER}'"))
    else:
        cols = {c["name"]: c for c in insp.get_columns("vehicle_inspections")}
        col_names = set(cols)

        if "status" not in col_names:
            op.add_column(
                "vehicle_inspections",
                sa.Column("status", sa.String(length=30), nullable=False, server_default="IN_PROGRESS"),
            )
            op.execute(
                sa.text(
                    """
                    UPDATE vehicle_inspections
                    SET status = 'COMPLETED'
                    WHERE result IS NOT NULL AND trim(result::text) <> ''
                    """
                )
            )
            op.execute(sa.text("ALTER TABLE vehicle_inspections ALTER COLUMN status DROP DEFAULT"))

        if "result" in cols and cols["result"].get("nullable") is False:
            op.alter_column(
                "vehicle_inspections",
                "result",
                existing_type=sa.String(length=10),
                nullable=True,
            )

        if "photo_urls" in col_names:
            op.drop_column("vehicle_inspections", "photo_urls")

        if "latitude" not in col_names:
            op.add_column("vehicle_inspections", sa.Column("latitude", sa.Float(), nullable=True))
        if "longitude" not in col_names:
            op.add_column("vehicle_inspections", sa.Column("longitude", sa.Float(), nullable=True))
        if "ip_address" not in col_names:
            op.add_column("vehicle_inspections", sa.Column("ip_address", sa.String(length=45), nullable=True))
        if "declaration_accepted" not in col_names:
            op.add_column(
                "vehicle_inspections",
                sa.Column("declaration_accepted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            )
            op.execute(sa.text("ALTER TABLE vehicle_inspections ALTER COLUMN declaration_accepted DROP DEFAULT"))
        if "signature_path" not in col_names:
            op.add_column("vehicle_inspections", sa.Column("signature_path", sa.String(length=500), nullable=True))

        insp = sa.inspect(conn)
        idx_names = {i["name"] for i in insp.get_indexes("vehicle_inspections")}
        if "ix_vehicle_inspections_status" not in idx_names:
            op.create_index("ix_vehicle_inspections_status", "vehicle_inspections", ["status"], unique=False)
        if "ix_vehicle_inspections_vehicle_driver" not in idx_names:
            op.create_index(
                "ix_vehicle_inspections_vehicle_driver",
                "vehicle_inspections",
                ["vehicle_id", "driver_id"],
                unique=False,
            )
        if "ix_vehicle_inspections_created_at" not in idx_names:
            op.create_index("ix_vehicle_inspections_created_at", "vehicle_inspections", ["created_at"], unique=False)

    insp = sa.inspect(conn)
    if insp.has_table("vehicle_defects"):
        dcols = {c["name"] for c in insp.get_columns("vehicle_defects")}
        if "inspection_id" not in dcols:
            op.add_column(
                "vehicle_defects",
                sa.Column(
                    "inspection_id",
                    UUID(as_uuid=False),
                    nullable=True,
                ),
            )
            op.create_foreign_key(
                "vehicle_defects_inspection_id_fkey",
                "vehicle_defects",
                "vehicle_inspections",
                ["inspection_id"],
                ["id"],
                ondelete="SET NULL",
            )
            op.create_index("ix_vehicle_defects_inspection_id", "vehicle_defects", ["inspection_id"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)

    if insp.has_table("vehicle_defects"):
        dcols = {c["name"] for c in insp.get_columns("vehicle_defects")}
        if "inspection_id" in dcols:
            op.drop_index("ix_vehicle_defects_inspection_id", table_name="vehicle_defects")
            op.drop_constraint("vehicle_defects_inspection_id_fkey", "vehicle_defects", type_="foreignkey")
            op.drop_column("vehicle_defects", "inspection_id")

    if not insp.has_table("vehicle_inspections"):
        return

    marker = conn.execute(
        sa.text("SELECT obj_description('public.vehicle_inspections'::regclass, 'pg_class')")
    ).scalar()
    if marker == _FULL_CREATE_MARKER:
        op.execute(sa.text("COMMENT ON TABLE vehicle_inspections IS NULL"))
        op.drop_table("vehicle_inspections")
        return

    idx_names = {i["name"] for i in insp.get_indexes("vehicle_inspections")}
    for name in ("ix_vehicle_inspections_created_at", "ix_vehicle_inspections_vehicle_driver", "ix_vehicle_inspections_status"):
        if name in idx_names:
            op.drop_index(name, table_name="vehicle_inspections")

    cols = {c["name"] for c in insp.get_columns("vehicle_inspections")}
    for col in ("signature_path", "declaration_accepted", "ip_address", "longitude", "latitude"):
        if col in cols:
            op.drop_column("vehicle_inspections", col)

    if "photo_urls" not in cols:
        op.add_column(
            "vehicle_inspections",
            sa.Column("photo_urls", ARRAY(sa.String()), nullable=True),
        )

    result_col = next((c for c in insp.get_columns("vehicle_inspections") if c["name"] == "result"), None)
    if result_col is not None and result_col.get("nullable") is True:
        op.alter_column(
            "vehicle_inspections",
            "result",
            existing_type=sa.String(length=10),
            nullable=False,
        )

    if "status" in cols:
        op.drop_column("vehicle_inspections", "status")
