"""org_contract_title_expiry.

Revision ID: 0075_org_contract_title_expiry
Revises: 0074_qb_sync_controls_and_mappings
Create Date: 2026-04-17 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0075_org_contract_title_expiry"
down_revision: Union[str, None] = "0074_qb_sync_controls"
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("contract_title", sa.String(255), nullable=True))
    op.add_column("organizations", sa.Column("contract_expiry_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "contract_expiry_date")
    op.drop_column("organizations", "contract_title")
