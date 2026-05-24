"""Add published_by_id to vehicle_drafts for audit.

Records which user published the draft.

Revision ID: 0039_vehicle_draft_published_by
Revises: 0038_route_events_and_route_code
Create Date: 2026-03-31 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0039_vehicle_draft_published_by"
down_revision: Union[str, None] = "0038_route_events_and_route_code"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vehicle_drafts",
        sa.Column("published_by_id", sa.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "vehicle_drafts_published_by_id_fkey",
        "vehicle_drafts",
        "users",
        ["published_by_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("vehicle_drafts_published_by_id_fkey", "vehicle_drafts", type_="foreignkey")
    op.drop_column("vehicle_drafts", "published_by_id")
