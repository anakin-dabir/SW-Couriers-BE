"""org_notes_refactor

Refactors the org notes + tags schema:

1. Drops org_note_tag_links (tags no longer attach to individual notes).
2. Creates org_tag_org_links — tags now attach to organisations.
3. Adds notecategory enum + category column to org_notes.
4. Creates org_note_mentions — @mention junction (note ↔ user, in-app only).

Revision ID: 0053_org_notes_refactor
Revises: 0052_users_session_sv
Create Date: 2026-04-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0053_org_notes_refactor"
down_revision = "0052_users_session_sv"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop the old note↔tag junction
    op.drop_table("org_note_tag_links")

    # 2. Create the new tag↔org junction
    op.create_table(
        "org_tag_org_links",
        sa.Column(
            "tag_id",
            UUID(as_uuid=False),
            sa.ForeignKey("org_note_tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=False),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_org_tag_org_links_org_id",
        "org_tag_org_links",
        ["organization_id"],
    )

    # 3. Add notecategory enum + category column to org_notes
    notecategory = sa.Enum(
        "GENERAL",
        "MEETING_NOTES",
        "PHONE_CALL",
        "ESCALATION",
        "COMPLIANCE",
        "COMMERCIAL",
        name="notecategory",
    )
    notecategory.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "org_notes",
        sa.Column(
            "category",
            notecategory,
            nullable=False,
            server_default="GENERAL",
        ),
    )

    # 4. Create org_note_mentions junction
    op.create_table(
        "org_note_mentions",
        sa.Column(
            "note_id",
            UUID(as_uuid=False),
            sa.ForeignKey("org_notes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_org_note_mentions_note_id",
        "org_note_mentions",
        ["note_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_org_note_mentions_note_id", table_name="org_note_mentions")
    op.drop_table("org_note_mentions")

    op.drop_column("org_notes", "category")
    sa.Enum(name="notecategory").drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_org_tag_org_links_org_id", table_name="org_tag_org_links")
    op.drop_table("org_tag_org_links")

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
