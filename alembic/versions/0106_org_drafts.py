"""org drafts — draft-save flow for organisation creation

Revision ID: 0106_org_drafts
Revises: 0105_nullable_od_fields
Create Date: 2026-04-30

Changes:
  1. Make formerly-required columns on organizations nullable (draft support).
  2. Create org_draft_number_seq sequence for ORG-D-NNN codes.
  3. Create org_drafts pivot table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0106_org_drafts"
down_revision: Union[str, None] = "0105_nullable_od_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 0. Extend the status CHECK constraint to include DRAFT ────────────────
    op.execute("ALTER TABLE organizations DROP CONSTRAINT IF EXISTS ck_organizations_status")
    op.execute(
        "ALTER TABLE organizations ADD CONSTRAINT ck_organizations_status "
        "CHECK (status IN ('DRAFT', 'ACTIVE', 'ON_HOLD', 'SUSPENDED', 'INACTIVE'))"
    )

    # ── 1. Make required organizations columns nullable for draft support ──────
    nullable_cols = [
        ("trading_name", sa.String(255)),
        ("legal_entity_name", sa.String(255)),
        ("industry", sa.String(100)),
        ("company_size", sa.String(50)),
        ("date_of_incorporation", sa.Date()),
        ("companies_house_number", sa.String(100)),
        ("reg_address_line_1", sa.String(255)),
        ("reg_city", sa.String(100)),
        ("reg_postcode", sa.String(20)),
    ]
    for col_name, col_type in nullable_cols:
        op.alter_column(
            "organizations",
            col_name,
            existing_type=col_type,
            nullable=True,
        )

    # ── 2. Sequence for ORG-D-NNN draft numbers ───────────────────────────────
    op.execute("CREATE SEQUENCE IF NOT EXISTS org_draft_number_seq START 1 INCREMENT 1")

    # ── 3. org_drafts pivot table ─────────────────────────────────────────────
    op.create_table(
        "org_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("draft_number", sa.String(20), nullable=True, unique=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "published_by_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("draft_contacts", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_org_drafts_draft_number", "org_drafts", ["draft_number"])
    op.create_index("ix_org_drafts_organization_id", "org_drafts", ["organization_id"])
    op.create_index("ix_org_drafts_created_by_id", "org_drafts", ["created_by_id"])


def downgrade() -> None:
    op.drop_table("org_drafts")
    op.execute("DROP SEQUENCE IF EXISTS org_draft_number_seq")

    # Restore original status CHECK constraint (no DRAFT)
    op.execute("ALTER TABLE organizations DROP CONSTRAINT IF EXISTS ck_organizations_status")
    op.execute(
        "ALTER TABLE organizations ADD CONSTRAINT ck_organizations_status "
        "CHECK (status IN ('ACTIVE', 'ON_HOLD', 'SUSPENDED', 'INACTIVE'))"
    )

    # Restore NOT NULL constraints (will fail if any DRAFT rows exist — clean those first)
    nullable_cols = [
        ("trading_name", sa.String(255)),
        ("legal_entity_name", sa.String(255)),
        ("industry", sa.String(100)),
        ("company_size", sa.String(50)),
        ("date_of_incorporation", sa.Date()),
        ("companies_house_number", sa.String(100)),
        ("reg_address_line_1", sa.String(255)),
        ("reg_city", sa.String(100)),
        ("reg_postcode", sa.String(20)),
    ]
    for col_name, col_type in nullable_cols:
        op.alter_column(
            "organizations",
            col_name,
            existing_type=col_type,
            nullable=False,
        )
