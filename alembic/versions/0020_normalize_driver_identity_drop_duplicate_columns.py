"""Normalize driver identity by removing duplicated profile columns.

Revision ID: 0020_normalize_driver_identity
Revises: 0019_merge_alembic_heads
Create Date: 2026-03-23
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_normalize_driver_identity"
down_revision: str | None = "0019_merge_alembic_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Use IF EXISTS so the migration is resilient in drifted environments.
    # This keeps upgrades safe even when some columns were already removed.
    op.execute("ALTER TABLE IF EXISTS drivers DROP COLUMN IF EXISTS first_name")
    op.execute("ALTER TABLE IF EXISTS drivers DROP COLUMN IF EXISTS last_name")
    op.execute("ALTER TABLE IF EXISTS drivers DROP COLUMN IF EXISTS email")
    op.execute("ALTER TABLE IF EXISTS drivers DROP COLUMN IF EXISTS phone")


def downgrade() -> None:
    # Use IF NOT EXISTS so downgrade is also safe in partially drifted schemas.
    op.execute("ALTER TABLE IF EXISTS drivers ADD COLUMN IF NOT EXISTS first_name VARCHAR(100)")
    op.execute("ALTER TABLE IF EXISTS drivers ADD COLUMN IF NOT EXISTS last_name VARCHAR(100)")
    op.execute("ALTER TABLE IF EXISTS drivers ADD COLUMN IF NOT EXISTS email VARCHAR(255)")
    op.execute("ALTER TABLE IF EXISTS drivers ADD COLUMN IF NOT EXISTS phone VARCHAR(50)")
