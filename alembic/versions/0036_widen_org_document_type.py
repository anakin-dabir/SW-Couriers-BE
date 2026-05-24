"""Widen org_documents.document_type column to VARCHAR(50).

Original column was VARCHAR(20) — too short for new enum values such as
COMPANY_REGISTRATION_CERT (25), EMPLOYERS_LIABILITY_INSURANCE (29), etc.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0036_widen_org_document_type"
down_revision: str | None = "0035_org_documents_v2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.alter_column(
        "org_documents",
        "document_type",
        existing_type=sa.String(20),
        type_=sa.String(50),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "org_documents",
        "document_type",
        existing_type=sa.String(50),
        type_=sa.String(20),
        existing_nullable=False,
    )
