"""Drop vehicle defect's defect_types which is vehicle_type INTERNAL | EXTERNAL

Revision ID: 0159_drop_vd_defact_types
Revises: 0158_backfill_audit_user_role
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0159_drop_vd_defact_types"
down_revision: str | None = "0158_backfill_audit_user_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("vehicle_defects", "defect_type")



def downgrade() -> None:
    op.add_column(
        "vehicle_defects",
        sa.Column(
            "defect_type",
            sa.Enum("INTERNAL", "EXTERNAL", name="vehicletype", native_enum=False),
            server_default="INTERNAL",
            nullable=False,
        ),
    )