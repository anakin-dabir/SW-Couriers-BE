"""Invites flow B: invite references existing user (set password / activate).

Revision ID: 0006_invites_flow_b
Revises: 0005_invites
Create Date: 2026-02-23

- Drop old invites columns (email, first_name, last_name, role, organization_id, region_id).
- Add user_id (FK to users, required). Invite = link for existing user to set password.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_invites_flow_b"
down_revision: str = "0005_invites"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("invites")

    op.create_table(
        "invites",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_invites_user_id", "invites", ["user_id"])
    op.create_index("ix_invites_token_hash", "invites", ["token_hash"], unique=True)
    op.create_index("ix_invites_invited_by_user_id", "invites", ["invited_by_user_id"])


def downgrade() -> None:
    op.drop_table("invites")

    op.create_table(
        "invites",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("region_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["region_id"],
            ["regions.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_invites_email", "invites", ["email"])
    op.create_index("ix_invites_role", "invites", ["role"])
    op.create_index("ix_invites_token_hash", "invites", ["token_hash"], unique=True)
    op.create_index("ix_invites_invited_by_user_id", "invites", ["invited_by_user_id"])
    op.create_index("ix_invites_organization_id", "invites", ["organization_id"])
    op.create_index("ix_invites_region_id", "invites", ["region_id"])
