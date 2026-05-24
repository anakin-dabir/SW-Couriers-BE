"""add max continuous driving hours and break duration to vehicles

Stores optional driving/break rule inputs (hours and minutes) used with service reminders.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0121_vehicle_driving_break_rules"
down_revision: str | None = "0120_admins_postal_address"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vehicles", sa.Column("max_continuous_driving_hours", sa.Float(), nullable=True))
    op.add_column("vehicles", sa.Column("break_duration_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("vehicles", "break_duration_minutes")
    op.drop_column("vehicles", "max_continuous_driving_hours")
