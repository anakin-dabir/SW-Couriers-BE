"""Create invites table for user invite flow.

Revision ID: 0005_invites
Revises: 0004_users_refresh
Create Date: 2026-02-23

- invites: email, first_name, last_name, role, token_hash, expires_at, used_at,
  invited_by_user_id, organization_id, region_id, created_at.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_invites"
down_revision: str = "0004_users_refresh"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("ix_invites_region_id", "invites")
    op.drop_index("ix_invites_organization_id", "invites")
    op.drop_index("ix_invites_invited_by_user_id", "invites")
    op.drop_index("ix_invites_token_hash", "invites")
    op.drop_index("ix_invites_role", "invites")
    op.drop_index("ix_invites_email", "invites")
    op.drop_table("invites")
