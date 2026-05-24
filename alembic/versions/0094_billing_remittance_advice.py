"""billing remittance advice attachment (R2)

Revision ID: 0094_billing_remittance
Revises: 0093_drv_operational_cfg
Create Date: 2026-04-27

Adds optional remittance advice document metadata and R2 object key on billing_payments.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0094_billing_remittance"
down_revision: Union[str, None] = "0093_drv_operational_cfg"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "billing_payments",
        sa.Column("remittance_advice_r2_key", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "billing_payments",
        sa.Column("remittance_advice_content_type", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "billing_payments",
        sa.Column("remittance_advice_original_filename", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "billing_payments",
        sa.Column("remittance_advice_size_bytes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "billing_payments",
        sa.Column("remittance_advice_uploaded_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("billing_payments", "remittance_advice_uploaded_at")
    op.drop_column("billing_payments", "remittance_advice_size_bytes")
    op.drop_column("billing_payments", "remittance_advice_original_filename")
    op.drop_column("billing_payments", "remittance_advice_content_type")
    op.drop_column("billing_payments", "remittance_advice_r2_key")
