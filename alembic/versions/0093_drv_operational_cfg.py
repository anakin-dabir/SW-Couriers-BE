"""Driver operational configuration: layover preferences.

Revision ID: 0093_drv_operational_cfg
Revises: 0092_share_otp_access_tokens
Create Date: 2026-04-27

Adds columns used by admin Driver Management (Add Driver + Edit Configurations modal).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0093_drv_operational_cfg"
down_revision: str | None = "0092_share_otp_access_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "drivers",
        sa.Column("okay_with_layover", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "drivers",
        sa.Column(
            "layover_cost_per_night",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "drivers",
        sa.Column(
            "max_layover_nights",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
def downgrade() -> None:
    op.drop_column("drivers", "max_layover_nights")
    op.drop_column("drivers", "layover_cost_per_night")
    op.drop_column("drivers", "okay_with_layover")
