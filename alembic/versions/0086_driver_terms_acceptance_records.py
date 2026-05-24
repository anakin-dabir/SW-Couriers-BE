"""driver_terms_acceptance_records

Revision ID: 0086_terms_accept_audit
Revises: 0085_notification_unified
Create Date: 2026-04-24

Append-only audit of each driver T&C acceptance (IP, User-Agent, client/device context,
optional per-install ``device_installation_id``, and partial index for device+hash lookups).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0086_terms_accept_audit"
down_revision: Union[str, None] = "0085_notification_unified"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "driver_terms_acceptance_records",
        sa.Column("driver_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("terms_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("client_type", sa.String(length=32), nullable=True),
        sa.Column("device_info", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("device_installation_id", sa.String(length=128), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["terms_id"], ["driver_terms_and_conditions.id"], ondelete="SET NULL"),
        # terms_id nullable so historical rows survive if terms config is removed
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_driver_terms_acceptance_records_driver_id",
        "driver_terms_acceptance_records",
        ["driver_id"],
        unique=False,
    )
    op.create_index(
        "ix_driver_terms_acceptance_records_terms_id",
        "driver_terms_acceptance_records",
        ["terms_id"],
        unique=False,
    )
    op.create_index(
        "ix_driver_terms_acceptance_records_created_at",
        "driver_terms_acceptance_records",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_dtar_driver_device_hash",
        "driver_terms_acceptance_records",
        ["driver_id", "device_installation_id", "content_hash"],
        unique=False,
        postgresql_where=sa.text("device_installation_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_dtar_driver_device_hash", table_name="driver_terms_acceptance_records")
    op.drop_index("ix_driver_terms_acceptance_records_created_at", table_name="driver_terms_acceptance_records")
    op.drop_index("ix_driver_terms_acceptance_records_terms_id", table_name="driver_terms_acceptance_records")
    op.drop_index("ix_driver_terms_acceptance_records_driver_id", table_name="driver_terms_acceptance_records")
    op.drop_table("driver_terms_acceptance_records")
