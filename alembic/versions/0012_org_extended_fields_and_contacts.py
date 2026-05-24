"""org_extended_fields_and_contacts

Adds extended company profile fields to organizations table and creates the
org_contacts table for multi-contact support.

Revision ID: 0012_org_extended
Revises: 0011_org_reference
Create Date: 2026-03-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_org_extended"
down_revision: str | None = "0011_org_reference"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Extend organizations table ────────────────────────────────────────────

    # Rename name → trading_name, legal_name → legal_entity_name
    op.alter_column("organizations", "name", new_column_name="trading_name")
    op.alter_column("organizations", "legal_name", new_column_name="legal_entity_name")

    # Rename registration_number → companies_house_number
    op.alter_column("organizations", "registration_number", new_column_name="companies_house_number")

    # date_of_incorporation was not in create_all_tables; add it before backfill
    op.add_column(
        "organizations",
        sa.Column("date_of_incorporation", sa.Date(), nullable=True),
    )

    # General information — make required fields NOT NULL (fill blanks first)
    op.execute("UPDATE organizations SET legal_entity_name = trading_name WHERE legal_entity_name IS NULL")
    op.execute("UPDATE organizations SET companies_house_number = '' WHERE companies_house_number IS NULL")
    op.execute("UPDATE organizations SET vat_number = '' WHERE vat_number IS NULL")
    op.execute("UPDATE organizations SET date_of_incorporation = '2000-01-01' WHERE date_of_incorporation IS NULL")

    op.alter_column("organizations", "legal_entity_name", nullable=False)
    op.alter_column("organizations", "companies_house_number", nullable=False)
    op.alter_column("organizations", "vat_number", nullable=False)
    op.alter_column("organizations", "date_of_incorporation", nullable=False)

    # Add new general info columns
    op.add_column("organizations", sa.Column("industry", sa.String(50), nullable=True))
    op.add_column("organizations", sa.Column("company_size", sa.String(50), nullable=True))
    op.add_column("organizations", sa.Column("website", sa.String(500), nullable=True))
    op.add_column("organizations", sa.Column("description", sa.String(500), nullable=True))

    # Pricing plans (JSON snapshot of assigned tiers)
    op.add_column("organizations", sa.Column("pricing_plans", postgresql.JSON(astext_type=sa.Text()), nullable=True))

    # Contract & agreement
    op.add_column("organizations", sa.Column("contract_reference", sa.String(500), nullable=True))
    op.add_column("organizations", sa.Column("pricing_agreement_start", sa.Date, nullable=True))
    op.add_column("organizations", sa.Column("pricing_agreement_end", sa.Date, nullable=True))

    # Package restrictions
    op.add_column("organizations", sa.Column("max_package_weight", sa.Float, nullable=True))
    op.add_column("organizations", sa.Column("max_package_length", sa.Float, nullable=True))
    op.add_column("organizations", sa.Column("max_package_width", sa.Float, nullable=True))
    op.add_column("organizations", sa.Column("max_package_height", sa.Float, nullable=True))
    op.add_column("organizations", sa.Column("min_charge_per_booking", sa.Numeric(10, 2), nullable=True))

    # Set defaults then make industry/company_size NOT NULL
    op.execute("UPDATE organizations SET industry = 'OTHER' WHERE industry IS NULL")
    op.execute("UPDATE organizations SET company_size = '1-10 employees' WHERE company_size IS NULL")
    op.alter_column("organizations", "industry", nullable=False)
    op.alter_column("organizations", "company_size", nullable=False)

    # Registration details
    op.add_column("organizations", sa.Column("eori_number", sa.String(100), nullable=True))

    # Registered address — make required fields NOT NULL
    op.add_column("organizations", sa.Column("reg_address_line_1", sa.String(255), nullable=True))
    op.add_column("organizations", sa.Column("reg_address_line_2", sa.String(255), nullable=True))
    op.add_column("organizations", sa.Column("reg_city", sa.String(100), nullable=True))
    op.add_column("organizations", sa.Column("reg_state", sa.String(100), nullable=True))
    op.add_column("organizations", sa.Column("reg_postcode", sa.String(20), nullable=True))
    op.add_column("organizations", sa.Column("reg_country", sa.String(100), nullable=True, server_default="United Kingdom"))

    op.execute("UPDATE organizations SET reg_address_line_1 = '' WHERE reg_address_line_1 IS NULL")
    op.execute("UPDATE organizations SET reg_city = '' WHERE reg_city IS NULL")
    op.execute("UPDATE organizations SET reg_postcode = '' WHERE reg_postcode IS NULL")
    op.alter_column("organizations", "reg_address_line_1", nullable=False)
    op.alter_column("organizations", "reg_city", nullable=False)
    op.alter_column("organizations", "reg_postcode", nullable=False)

    # Drop trading address columns
    # (trading address is captured from contacts / separate flow)

    # Drop old flat fields superseded by the structured address and contacts model
    op.drop_column("organizations", "billing_address") if sa.inspect else None
    op.drop_column("organizations", "contact_name") if sa.inspect else None
    op.drop_column("organizations", "contact_email") if sa.inspect else None
    op.drop_column("organizations", "contact_phone") if sa.inspect else None
    op.drop_column("organizations", "billing_email") if sa.inspect else None
    op.drop_column("organizations", "api_key_hash") if sa.inspect else None
    op.drop_column("organizations", "api_key_prefix") if sa.inspect else None

    # ── Create org_contacts table ─────────────────────────────────────────────
    op.create_table(
        "org_contacts",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("contact_number", sa.String(50), nullable=False),
        sa.Column("contact_role", sa.String(50), nullable=False, server_default="ACCOUNT_OWNER"),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING"),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_org_contacts_organization_id", "org_contacts", ["organization_id"])
    op.create_index("ix_org_contacts_user_id", "org_contacts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_org_contacts_user_id", table_name="org_contacts")
    op.drop_index("ix_org_contacts_organization_id", table_name="org_contacts")
    op.drop_table("org_contacts")

    op.alter_column("organizations", "trading_name", new_column_name="name")
    op.alter_column("organizations", "legal_entity_name", new_column_name="legal_name")
    op.alter_column("organizations", "companies_house_number", new_column_name="registration_number")

    op.drop_column("organizations", "date_of_incorporation")
    op.drop_column("organizations", "reg_postcode")
    op.drop_column("organizations", "reg_state")
    op.drop_column("organizations", "reg_city")
    op.drop_column("organizations", "reg_address_line_2")
    op.drop_column("organizations", "reg_address_line_1")
    op.drop_column("organizations", "reg_country")
    op.drop_column("organizations", "eori_number")
    op.drop_column("organizations", "min_charge_per_booking")
    op.drop_column("organizations", "max_package_height")
    op.drop_column("organizations", "max_package_width")
    op.drop_column("organizations", "max_package_length")
    op.drop_column("organizations", "max_package_weight")
    op.drop_column("organizations", "pricing_agreement_end")
    op.drop_column("organizations", "pricing_agreement_start")
    op.drop_column("organizations", "contract_reference")
    op.drop_column("organizations", "pricing_plans")
    op.drop_column("organizations", "description")
    op.drop_column("organizations", "website")
    op.drop_column("organizations", "company_size")
    op.drop_column("organizations", "industry")
