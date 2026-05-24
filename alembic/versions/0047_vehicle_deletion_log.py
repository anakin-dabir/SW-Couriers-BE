"""Vehicle deletion audit log (append-only snapshot before hard delete).

Revision ID: 0047_vehicle_deletion_log
Revises: 0046_org_pickup_addresses
Create Date: 2026-03-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0047_vehicle_deletion_log"
down_revision = "0046_org_pickup_addresses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vehicle_deletions",
        sa.Column("id", UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("vehicle_id", UUID(as_uuid=False), nullable=False),
        sa.Column("registration_number", sa.String(length=20), nullable=True),
        sa.Column("make", sa.String(length=100), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("vehicle_type", sa.String(length=30), nullable=True),
        sa.Column("deletion_reason", sa.Text(), nullable=False),
        sa.Column("deleted_by_id", UUID(as_uuid=False), nullable=True),
        sa.ForeignKeyConstraint(
            ["deleted_by_id"],
            ["users.id"],
            name="vehicle_deletions_deleted_by_id_fkey",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_vehicle_deletions_vehicle_id", "vehicle_deletions", ["vehicle_id"], unique=False)
    op.create_index("ix_vehicle_deletions_created_at", "vehicle_deletions", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_vehicle_deletions_created_at", table_name="vehicle_deletions")
    op.drop_index("ix_vehicle_deletions_vehicle_id", table_name="vehicle_deletions")
    op.drop_table("vehicle_deletions")
