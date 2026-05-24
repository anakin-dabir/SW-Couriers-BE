"""Store configurable vehicle dropdown values as strings.

Revision ID: 0136_vehicle_dropdown_strings
Revises: 0135_status_automation
Create Date: 2026-05-15

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0136_vehicle_dropdown_strings"
down_revision: Union[str, None] = "0135_status_automation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("vehicles", "fuel_type", existing_type=sa.String(length=20), type_=sa.String(length=64), existing_nullable=True)
    op.alter_column(
        "vehicles",
        "availability",
        existing_type=sa.Enum("ACTIVE", "UNAVAILABLE", "IN_MAINTENANCE", name="vehicleavailability", native_enum=False),
        type_=sa.String(length=64),
        existing_nullable=False,
        existing_server_default="ACTIVE",
    )
    op.alter_column(
        "vehicle_defects",
        "category",
        existing_type=sa.Enum(
            "ROUTINE_SERVICE",
            "TYRES",
            "PART_REPLACEMENT",
            "BREAKDOWN",
            "CABIN_EQUIPMENT",
            "LIGHTS_AND_INDICATORS",
            "BODY_DAMAGE",
            "MIRROR_AND_GLASS",
            "SAFETY_EQUIPMENT",
            "OTHER",
            name="defectcategory",
            native_enum=False,
        ),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "vehicle_service_records",
        "service_type",
        existing_type=sa.Enum(
            "INTERIM_SERVICE",
            "FULL_SERVICE",
            "MAJOR_SERVICE",
            "MANUFACTURER_SERVICE",
            name="servicetype",
            native_enum=False,
        ),
        type_=sa.String(length=64),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "vehicle_service_records",
        "service_type",
        existing_type=sa.String(length=64),
        type_=sa.Enum(
            "INTERIM_SERVICE",
            "FULL_SERVICE",
            "MAJOR_SERVICE",
            "MANUFACTURER_SERVICE",
            name="servicetype",
            native_enum=False,
        ),
        existing_nullable=False,
    )
    op.alter_column(
        "vehicle_defects",
        "category",
        existing_type=sa.String(length=64),
        type_=sa.Enum(
            "ROUTINE_SERVICE",
            "TYRES",
            "PART_REPLACEMENT",
            "BREAKDOWN",
            "CABIN_EQUIPMENT",
            "LIGHTS_AND_INDICATORS",
            "BODY_DAMAGE",
            "MIRROR_AND_GLASS",
            "SAFETY_EQUIPMENT",
            "OTHER",
            name="defectcategory",
            native_enum=False,
        ),
        existing_nullable=False,
    )
    op.alter_column(
        "vehicles",
        "availability",
        existing_type=sa.String(length=64),
        type_=sa.Enum("ACTIVE", "UNAVAILABLE", "IN_MAINTENANCE", name="vehicleavailability", native_enum=False),
        existing_nullable=False,
        existing_server_default="ACTIVE",
    )
    op.alter_column("vehicles", "fuel_type", existing_type=sa.String(length=64), type_=sa.String(length=20), existing_nullable=True)
