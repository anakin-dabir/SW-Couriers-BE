"""merge org docs and driver draft heads

Revision ID: 0032_merge_org_driver_heads
Revises: 0029_org_documents, 0031_draft_backfill
Create Date: 2026-03-27 12:48:39.281570

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0032_merge_org_driver_heads'
down_revision: tuple[str, ...] = ('0029_org_documents', '0031_draft_backfill')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
