"""backfill legacy route plan status PUBLISHED to READY

Revision ID: 0134_backfill_route_plan
Revises: 0133_activation_link_requests
Create Date: 2026-05-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0134_backfill_route_plan"
down_revision: str | None = "0133_activation_link_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Legacy status value from older planning lifecycle; normalize to current READY state.
    op.execute("UPDATE route_plans SET status = 'READY' WHERE status = 'PUBLISHED'")


def downgrade() -> None:
    # Best-effort rollback for rows rewritten by this migration.
    op.execute("UPDATE route_plans SET status = 'PUBLISHED' WHERE status = 'READY'")
