"""Add shared activation link request work items.

Revision ID: 0133_activation_link_requests
Revises: 0132_status_automation_rules
Create Date: 2026-05-15

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

activation_link_request_status = sa.Enum(
    "PENDING",
    "RESOLVED",
    name="activationlinkrequeststatus",
    native_enum=False,
)

revision: str = "0133_activation_link_requests"
down_revision: Union[str, None] = "0132_status_automation_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activation_link_requests",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("requester_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("status", activation_link_request_status, nullable=False),
        sa.Column("resolved_by_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("resolved_invite_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_invite_id"], ["invites.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_activation_link_requests_one_pending_per_user",
        "activation_link_requests",
        ["requester_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index("uq_activation_link_requests_one_pending_per_user", table_name="activation_link_requests")
    op.drop_table("activation_link_requests")
