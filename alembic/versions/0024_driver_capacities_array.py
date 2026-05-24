"""Add capacities array to drivers and backfill from legacy capacity. Normalize driver documents kind enum.

This migration:
1. Adds a new array column to drivers to store multiple capacities (VAN/TRUCK).
2. Backfills the new column from the existing single capacity column.
3. Converts the driver_documents.kind column from a Postgres enum to a plain VARCHAR(40).
4. Collapses deprecated document kinds (CPC_CERTIFICATE, DIGITAL_TACHOGRAPH) into CUSTOM to preserve existing rows.
5. Drops the old Postgres enum type once no columns depend on it.
6. Enforces allowed kinds at rest (matches application enums after migration).

Revision ID: 0024_driver_capacities_array
Revises: e9f3b2c4d5e6
Create Date: 2026-03-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_driver_capacities_array"
down_revision: str | None = "e9f3b2c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add the new capacities array and backfill from legacy single capacity.
    op.add_column(
        "drivers",
        sa.Column(
            "capacities",
            postgresql.ARRAY(sa.String(length=20)),
            nullable=True,
        ),
    )
    op.execute("UPDATE drivers SET capacities = ARRAY[capacity]::varchar[] WHERE capacity IS NOT NULL")
    op.execute("UPDATE drivers SET capacities = ARRAY['VAN']::varchar[] WHERE capacities IS NULL")
    op.alter_column("drivers", "capacities", nullable=False, server_default=sa.text("ARRAY['VAN']::varchar[]"))

    # Normalize driver document kinds to simplified contract and remove DB enum coupling.
    # 1) Convert enum column -> plain text so future document types don't require enum migrations.
    op.execute(
        """
        ALTER TABLE driver_documents
        ALTER COLUMN kind TYPE VARCHAR(40)
        USING kind::text
        """
    )
    # 2) Collapse deprecated document kinds into CUSTOM to preserve existing rows.
    op.execute(
        """
        UPDATE driver_documents
        SET kind = 'CUSTOM'
        WHERE kind IN ('CPC_CERTIFICATE', 'DIGITAL_TACHOGRAPH')
        """
    )
    # 3) Drop the old Postgres enum type once no columns depend on it.
    op.execute("DROP TYPE IF EXISTS driver_document_kind_enum")
    # 4) Enforce allowed kinds at rest (matches application enums after migration).
    op.create_check_constraint(
        "ck_driver_documents_kind_driving_licence_or_custom",
        "driver_documents",
        "kind IN ('DRIVING_LICENCE', 'CUSTOM')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_driver_documents_kind_driving_licence_or_custom", "driver_documents", type_="check")
    # Recreate old enum type for backward compatibility.
    driver_document_kind_enum = sa.Enum(
        "DRIVING_LICENCE",
        "CPC_CERTIFICATE",
        "DIGITAL_TACHOGRAPH",
        "CUSTOM",
        name="driver_document_kind_enum",
    )
    driver_document_kind_enum.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "driver_documents",
        "kind",
        existing_type=sa.String(length=40),
        type_=driver_document_kind_enum,
        existing_nullable=False,
        postgresql_using=(
            "CASE "
            "WHEN kind IN ('DRIVING_LICENCE', 'CUSTOM') THEN kind "
            "ELSE 'CUSTOM' END::driver_document_kind_enum"
        ),
    )
    # Remove capacities array and return to the previous driver schema.
    op.drop_column("drivers", "capacities")
