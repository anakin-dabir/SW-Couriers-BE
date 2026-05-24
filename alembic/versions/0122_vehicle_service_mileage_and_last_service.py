"""add last_service_mileage to vehicles and mileage_at_service to service records

Backfill last_service_mileage from current_mileage. Backfill next_service_due from
interval months where missing.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0122_vehicle_svc_mileage"
down_revision: str | None = "0121_vehicle_driving_break_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vehicles", sa.Column("last_service_mileage", sa.Integer(), nullable=True))
    op.add_column("vehicle_service_records", sa.Column("mileage_at_service", sa.Integer(), nullable=True))

    op.execute(
        sa.text(
            "UPDATE vehicles SET last_service_mileage = COALESCE(current_mileage, 0) "
            "WHERE last_service_mileage IS NULL"
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE vehicles
            SET next_service_due = (CURRENT_DATE + (service_interval_months || ' months')::interval)::date
            WHERE next_service_due IS NULL
              AND service_interval_months IS NOT NULL
              AND service_interval_months > 0
            """
        )
    )


def downgrade() -> None:
    op.drop_column("vehicle_service_records", "mileage_at_service")
    op.drop_column("vehicles", "last_service_mileage")
