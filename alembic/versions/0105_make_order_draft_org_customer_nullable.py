"""make order draft org and customer nullable

Revision ID: 0105_nullable_od_fields
Revises: 0104_order_entity_status_events
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0105_nullable_od_fields"
down_revision: str | None = "0104_order_entity_status_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "order_drafts",
        "organization_id",
        existing_type=UUID(as_uuid=False),
        nullable=True,
    )
    op.alter_column(
        "order_drafts",
        "customer_id",
        existing_type=UUID(as_uuid=False),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "order_drafts",
        "customer_id",
        existing_type=UUID(as_uuid=False),
        nullable=False,
    )
    op.alter_column(
        "order_drafts",
        "organization_id",
        existing_type=UUID(as_uuid=False),
        nullable=False,
    )
