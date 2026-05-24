"""org_reference_and_crud

Adds auto-generated reference column (SWC-ORG-NNNNN) to organizations table.
The sequence org_ref_seq is used to generate monotonic reference numbers.

Revision ID: 0011_org_reference
Revises: 84429907deda
Create Date: 2026-03-10

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_org_reference"
down_revision: str | None = "84429907deda"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Sequence for SWC-ORG-NNNNN references
    op.execute("CREATE SEQUENCE IF NOT EXISTS org_ref_seq START 1 INCREMENT 1")

    op.add_column(
        "organizations",
        sa.Column("reference", sa.String(length=20), nullable=True),
    )

    # Back-fill existing rows with unique references
    op.execute("""
        UPDATE organizations
        SET reference = 'SWC-ORG-' || LPAD(nextval('org_ref_seq')::text, 5, '0')
        WHERE reference IS NULL
        """)

    op.create_unique_constraint("uq_organizations_reference", "organizations", ["reference"])
    op.create_index("ix_organizations_reference", "organizations", ["reference"])


def downgrade() -> None:
    op.drop_index("ix_organizations_reference", table_name="organizations")
    op.drop_constraint("uq_organizations_reference", "organizations", type_="unique")
    op.drop_column("organizations", "reference")
    op.execute("DROP SEQUENCE IF EXISTS org_ref_seq")
