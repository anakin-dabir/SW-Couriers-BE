"""pickup_addresses table + bookings.pickup_address_id -> pickup_addresses

Replaces legacy bookings.pickup_address_id (FK to addresses from da815) with
pickup_address_id FK to pickup_addresses. Runs after the billing/credit chain
(0081). The bookings table still exists at this point; the consolidated orders
migration (0083) does the bookings -> orders rename and the rest.

Revision ID: 0082_pickup_addresses
Revises: 0081_orgs_company_size_enum
"""

from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2.types
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0082_pickup_addresses"
down_revision: str | None = "0081_orgs_company_size_enum"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pickup_addresses",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.Column("contact_first_name", sa.String(length=100), nullable=False),
        sa.Column("contact_last_name", sa.String(length=100), nullable=False),
        sa.Column("contact_phone", sa.String(length=50), nullable=False),
        sa.Column("building_number", sa.String(length=100), nullable=True),
        sa.Column("line_1", sa.String(length=255), nullable=False),
        sa.Column("line_2", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("county", sa.String(length=100), nullable=True),
        sa.Column("postcode", sa.String(length=20), nullable=False),
        sa.Column("country", sa.String(length=100), nullable=False, server_default="United Kingdom"),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column(
            "location",
            geoalchemy2.types.Geometry(
                geometry_type="POINT",
                srid=4326,
                from_text="ST_GeomFromEWKT",
                name="geometry",
            ),
            nullable=True,
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "(organization_id IS NOT NULL AND user_id IS NULL) OR (organization_id IS NULL AND user_id IS NOT NULL)",
            name="ck_pickup_addresses_org_xor_user",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pickup_addresses_organization_id", "pickup_addresses", ["organization_id"], unique=False)
    op.create_index("ix_pickup_addresses_user_id", "pickup_addresses", ["user_id"], unique=False)
    op.create_index("ix_pickup_addresses_postcode", "pickup_addresses", ["postcode"], unique=False)
    op.create_index("ix_pickup_addresses_is_default", "pickup_addresses", ["is_default"], unique=False)

    op.drop_constraint("bookings_pickup_address_id_fkey", "bookings", type_="foreignkey")
    op.drop_column("bookings", "pickup_address_id")

    op.add_column(
        "bookings",
        sa.Column("pickup_address_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_bookings_pickup_address_id",
        "bookings",
        "pickup_addresses",
        ["pickup_address_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_bookings_pickup_address_id", "bookings", ["pickup_address_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_bookings_pickup_address_id", table_name="bookings")
    op.drop_constraint("fk_bookings_pickup_address_id", "bookings", type_="foreignkey")
    op.drop_column("bookings", "pickup_address_id")

    op.add_column(
        "bookings",
        sa.Column("pickup_address_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "bookings_pickup_address_id_fkey",
        "bookings",
        "addresses",
        ["pickup_address_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_index("ix_pickup_addresses_is_default", table_name="pickup_addresses")
    op.drop_index("ix_pickup_addresses_postcode", table_name="pickup_addresses")
    op.drop_index("ix_pickup_addresses_user_id", table_name="pickup_addresses")
    op.drop_index("ix_pickup_addresses_organization_id", table_name="pickup_addresses")
    op.drop_table("pickup_addresses")
