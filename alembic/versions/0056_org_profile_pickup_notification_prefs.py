"""org profile: pickup label only

Adds optional ``label`` on org pickup addresses. Receiver notification master
toggles use existing ``recipient_notification_preferences`` (no new table).

Revision ID: 0056_org_profile
Revises: 0055_payment_billing_address
Create Date: 2026-04-03

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0056_org_profile"
down_revision: str | None = "0055_payment_billing_address"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("org_pickup_addresses", sa.Column("label", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("org_pickup_addresses", "label")
