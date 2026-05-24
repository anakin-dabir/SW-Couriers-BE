"""org_notes_and_tags

Creates three tables for internal admin sticky notes on organisation profiles:
  - org_note_tags      — reusable category tags (VIP, High Volume, Meeting Notes, etc.)
  - org_notes          — internal notes per organisation (admin-only, soft-delete)
  - org_note_tag_links — many-to-many junction between notes and tags

Notes are never exposed to CUSTOMER_B2B endpoints.

Revision ID: 0022_org_notes_and_tags
Revises: 0021_db_sequences_for_codes
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0022_org_notes_and_tags"
down_revision = "0021_db_sequences_for_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── org_note_tags ─────────────────────────────────────────────────
    op.create_table(
        "org_note_tags",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("color", sa.String(30), nullable=False, server_default="#6B7280"),
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
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("name", name="uq_org_note_tags_name"),
    )

    # ── org_notes ─────────────────────────────────────────────────────
    op.create_table(
        "org_notes",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=False),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index(
        "ix_org_notes_organization_id",
        "org_notes",
        ["organization_id"],
    )
    # Partial index: active notes only — speeds up the common list query
    op.create_index(
        "ix_org_notes_org_active",
        "org_notes",
        ["organization_id", "is_pinned", "created_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ── org_note_tag_links ────────────────────────────────────────────
    op.create_table(
        "org_note_tag_links",
        sa.Column(
            "note_id",
            UUID(as_uuid=False),
            sa.ForeignKey("org_notes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            UUID(as_uuid=False),
            sa.ForeignKey("org_note_tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
     op.drop_table("org_note_tag_links")
     op.drop_index("ix_org_notes_org_active", table_name="org_notes")
     op.drop_index("ix_org_notes_organization_id", table_name="org_notes")
     op.drop_table("org_notes")
     op.drop_table("org_note_tags")
  