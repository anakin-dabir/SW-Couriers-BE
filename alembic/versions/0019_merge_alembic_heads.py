"""Merge Alembic heads

Revision ID: 0019_merge_alembic_heads
Revises: 0018_org_credit_suspension, a1b2c3d4e5f7
Create Date: 2026-03-23 15:21:28.187513

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0019_merge_alembic_heads"
down_revision: tuple[str, ...] = ("0018_org_credit_suspension", "a1b2c3d4e5f7")
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
