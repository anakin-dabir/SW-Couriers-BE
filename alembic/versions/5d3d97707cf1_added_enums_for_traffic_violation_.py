"""added enums for  traffic violation status and traffic violation type in DriverTrafficViolation Table

Revision ID: 5d3d97707cf1
Revises: dc6f35907587
Create Date: 2026-03-11 15:01:21.884496

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d3d97707cf1"
down_revision: str | None = "dc6f35907587"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade driver_traffic_violations to use PostgreSQL ENUM types for violation_type and status."""

    bind = op.get_bind()

    traffic_violation_type_enum = sa.Enum(
        "SPEEDING",
        "RED_LIGHT",
        "PARKING",
        "BUS_LANE",
        name="traffic_violation_type_enum",
    )
    traffic_violation_status_enum = sa.Enum(
        "UNPAID",
        "PAID",
        name="traffic_violation_status_enum",
    )

    # Create enum types if they don't exist yet.
    traffic_violation_type_enum.create(bind, checkfirst=True)
    traffic_violation_status_enum.create(bind, checkfirst=True)

    # Alter columns to use the new enum types.
    op.alter_column(
        "driver_traffic_violations",
        "violation_type",
        existing_type=sa.VARCHAR(length=50),
        type_=traffic_violation_type_enum,
        existing_nullable=False,
        postgresql_using="violation_type::traffic_violation_type_enum",
    )
    op.alter_column(
        "driver_traffic_violations",
        "status",
        existing_type=sa.VARCHAR(length=20),
        type_=traffic_violation_status_enum,
        existing_nullable=False,
        postgresql_using="status::traffic_violation_status_enum",
    )


def downgrade() -> None:
    """Revert driver_traffic_violations to use VARCHAR types and drop enum types."""

    bind = op.get_bind()

    traffic_violation_type_enum = sa.Enum(
        "SPEEDING",
        "RED_LIGHT",
        "PARKING",
        "BUS_LANE",
        name="traffic_violation_type_enum",
    )
    traffic_violation_status_enum = sa.Enum(
        "UNPAID",
        "PAID",
        name="traffic_violation_status_enum",
    )

    # First, revert columns back to VARCHAR.
    op.alter_column(
        "driver_traffic_violations",
        "status",
        existing_type=traffic_violation_status_enum,
        type_=sa.VARCHAR(length=20),
        existing_nullable=False,
    )
    op.alter_column(
        "driver_traffic_violations",
        "violation_type",
        existing_type=traffic_violation_type_enum,
        type_=sa.VARCHAR(length=50),
        existing_nullable=False,
    )

    # Then drop the enum types.
    traffic_violation_status_enum.drop(bind, checkfirst=True)
    traffic_violation_type_enum.drop(bind, checkfirst=True)
