"""add vehicle driver service due alert tracking

Revision ID: 0125_vehicle_service_alert
Revises: 0124_vehicle_defect_ref
Create Date: 2026-05-13 12:28:22.110619

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0125_vehicle_service_alert'
down_revision: str | None = '0124_vehicle_defect_ref'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vehicles",
        sa.Column(
            "driver_service_alert_sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("vehicles", "driver_service_alert_sent_at")
