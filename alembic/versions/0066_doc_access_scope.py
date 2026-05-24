"""Add access_scope to doc_otps and doc_access_tokens for driver document OTP flow.

Revision ID: 0066_doc_access_scope
Revises: 0065_org_vat_number_nullable
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0066_doc_access_scope"
down_revision: Union[str, None] = "0065_org_vat_number_nullable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "doc_otps",
        sa.Column(
            "access_scope",
            sa.String(length=30),
            nullable=False,
            server_default="ORG_DOCUMENTS",
        ),
    )

    op.add_column(
        "doc_access_tokens",
        sa.Column(
            "access_scope",
            sa.String(length=30),
            nullable=False,
            server_default="ORG_DOCUMENTS",
        ),
    )


def downgrade() -> None:
    op.drop_column("doc_access_tokens", "access_scope")
    op.drop_column("doc_otps", "access_scope")
