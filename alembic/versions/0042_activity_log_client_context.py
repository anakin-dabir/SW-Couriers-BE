"""activity_log_client_context

Add ip_address, browser, device, os columns to org_document_activities.
Also adds VIEWED and SHARED to the activity_type enum (no-op on VARCHAR columns).

Revision ID: 0042_activity_log_client_context
Revises: 0041_doc_access
Create Date: 2026-04-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0042_activity_log_client_context"
down_revision = "0041_doc_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("org_document_activities", sa.Column("ip_address", sa.String(45), nullable=True))
    op.add_column("org_document_activities", sa.Column("browser", sa.String(100), nullable=True))
    op.add_column("org_document_activities", sa.Column("device", sa.String(100), nullable=True))
    op.add_column("org_document_activities", sa.Column("os", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("org_document_activities", "os")
    op.drop_column("org_document_activities", "device")
    op.drop_column("org_document_activities", "browser")
    op.drop_column("org_document_activities", "ip_address")
