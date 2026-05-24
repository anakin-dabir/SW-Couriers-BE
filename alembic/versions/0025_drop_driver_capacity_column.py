"""Drop legacy drivers.capacity single-value column.

After introducing drivers.capacities (array) the single-value `capacity` column is redundant.
Application responses still include `capacity` as a derived value (first element of capacities),
but the column is removed at rest.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0025_drop_driver_capacity_column"
down_revision: str | None = "0024_driver_capacities_array"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Safe for dev environments where the column might already be dropped.
    op.execute("ALTER TABLE drivers DROP COLUMN IF EXISTS capacity")


def downgrade() -> None:
    # Recreate capacity from the first capacities element for rollback/testing purposes.
    op.execute(
        "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS capacity VARCHAR(20) NOT NULL DEFAULT 'VAN'"
    )
    # Ensure existing rows and any new rows have a value consistent with capacities[0].
    op.execute("UPDATE drivers SET capacity = capacities[0] WHERE capacities IS NOT NULL")

