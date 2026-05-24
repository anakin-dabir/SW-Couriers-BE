"""Migrate org_pickup_addresses into pickup_addresses, drop org table and contact columns.

Adds pickup_addresses.state, backfills from county, drops county and building_number.

Revision ID: 0100_pickup_consolidation
Revises: 0099_credit_cards
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import column, delete, table, update
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0100_pickup_consolidation"
down_revision: str | None = "0099_credit_cards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_dash = "\u2014"


def upgrade() -> None:
    op.add_column(
        "pickup_addresses",
        sa.Column("state", sa.String(length=100), nullable=True),
    )

    _pa_county = table(
        "pickup_addresses",
        column("state", sa.String(100)),
        column("county", sa.String(100)),
    )
    op.execute(
        update(_pa_county).values(
            state=_pa_county.c.county,
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO pickup_addresses (
                id, organization_id, user_id, label,
                contact_first_name, contact_last_name, contact_phone,
                building_number, line_1, line_2, city, state, postcode, country,
                latitude, longitude, location, is_default, created_by_user_id,
                created_at, updated_at, version
            )
            SELECT
                gen_random_uuid(),
                organization_id,
                NULL,
                label,
                '—', '—', '—',
                NULL,
                address_line_1,
                address_line_2,
                city,
                state,
                postcode,
                country,
                latitude,
                longitude,
                CASE
                    WHEN latitude IS NOT NULL AND longitude IS NOT NULL THEN
                        ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geometry
                    ELSE NULL
                END,
                is_default,
                NULL,
                created_at,
                updated_at,
                version
            FROM org_pickup_addresses
            """
        )
    )

    _pa = table(
        "pickup_addresses",
        column("contact_first_name", sa.String(100)),
        column("contact_last_name", sa.String(100)),
        column("contact_phone", sa.String(50)),
    )
    op.execute(
        update(_pa).values(
            contact_first_name=_dash,
            contact_last_name=_dash,
            contact_phone=_dash,
        )
    )

    op.drop_table("org_pickup_addresses")
    op.drop_column("pickup_addresses", "contact_first_name")
    op.drop_column("pickup_addresses", "contact_last_name")
    op.drop_column("pickup_addresses", "contact_phone")

    op.drop_column("pickup_addresses", "county")
    op.drop_column("pickup_addresses", "building_number")


def downgrade() -> None:
    op.add_column(
        "pickup_addresses",
        sa.Column("building_number", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "pickup_addresses",
        sa.Column("county", sa.String(length=100), nullable=True),
    )
    _pa_fill = table(
        "pickup_addresses",
        column("county", sa.String(100)),
        column("state", sa.String(100)),
    )
    op.execute(
        update(_pa_fill).values(
            county=_pa_fill.c.state,
        )
    )

    op.add_column(
        "pickup_addresses",
        sa.Column(
            "contact_first_name",
            sa.String(length=100),
            nullable=False,
            server_default=_dash,
        ),
    )
    op.add_column(
        "pickup_addresses",
        sa.Column(
            "contact_last_name",
            sa.String(length=100),
            nullable=False,
            server_default=_dash,
        ),
    )
    op.add_column(
        "pickup_addresses",
        sa.Column(
            "contact_phone",
            sa.String(length=50),
            nullable=False,
            server_default=_dash,
        ),
    )

    op.create_table(
        "org_pickup_addresses",
        sa.Column("id", UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            UUID(as_uuid=False),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(100), nullable=True),
        sa.Column("address_line_1", sa.String(255), nullable=False),
        sa.Column("address_line_2", sa.String(255), nullable=True),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("state", sa.String(100), nullable=True),
        sa.Column("postcode", sa.String(20), nullable=False),
        sa.Column("country", sa.String(100), nullable=False, server_default="United Kingdom"),
        sa.Column("latitude", sa.Float, nullable=True),
        sa.Column("longitude", sa.Float, nullable=True),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_index("ix_org_pickup_addresses_is_default", "org_pickup_addresses", ["is_default"])
    op.create_index(
        "ix_org_pickup_addresses_organization_id",
        "org_pickup_addresses",
        ["organization_id"],
        unique=False,
    )

    op.execute(
        sa.text(
            """
            INSERT INTO org_pickup_addresses (
                id, organization_id, label, address_line_1, address_line_2, city, state, postcode, country,
                latitude, longitude, is_default, created_at, updated_at, version
            )
            SELECT
                id, organization_id, label, line_1, line_2, city, state, postcode, country,
                latitude, longitude, is_default, created_at, updated_at, version
            FROM pickup_addresses
            WHERE organization_id IS NOT NULL
            """
        )
    )

    _pa_del = table("pickup_addresses", column("organization_id", UUID(as_uuid=False)))
    op.execute(delete(_pa_del).where(_pa_del.c.organization_id.isnot(None)))

    op.drop_column("pickup_addresses", "state")

    op.alter_column(
        "pickup_addresses",
        "contact_first_name",
        existing_type=sa.String(length=100),
        existing_nullable=False,
        server_default=None,
    )
    op.alter_column(
        "pickup_addresses",
        "contact_last_name",
        existing_type=sa.String(length=100),
        existing_nullable=False,
        server_default=None,
    )
    op.alter_column(
        "pickup_addresses",
        "contact_phone",
        existing_type=sa.String(length=50),
        existing_nullable=False,
        server_default=None,
    )
