"""Create user_permissions table for ACL overrides.

Revision ID: 0009_user_permissions
Revises: 6cc1ccedde23
Create Date: 2026-03-08

Stores per-user permission overrides. Only rows that differ from the
role's default permission matrix are stored here. If a user has no row
for a resource, the hardcoded default for their role applies.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0009_user_permissions"
down_revision: str = "6cc1ccedde23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_permissions",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("resource", sa.String(50), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column(
            "granted_by",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint("user_id", "resource", name="uq_user_permissions_user_resource"),
    )


def downgrade() -> None:
    op.drop_table("user_permissions")
