"""Add required garage column to vehicle_maintenance_records.

Revision ID: 0050_vm_garage
Revises: 0047_vehicle_deletion_log
Create Date: 2026-03-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0050_vm_garage"
down_revision = "0049_users_last_login"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vehicle_maintenance_records",
        sa.Column("garage", sa.String(length=255), nullable=True),
    )
    op.execute(sa.text("UPDATE vehicle_maintenance_records SET garage = 'Unknown' WHERE garage IS NULL"))
    op.alter_column(
        "vehicle_maintenance_records",
        "garage",
        existing_type=sa.String(length=255),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("vehicle_maintenance_records", "garage")
