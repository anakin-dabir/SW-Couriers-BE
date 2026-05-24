"""Driver documents kind as PostgreSQL enum

Revision ID: a1b2c3d4e5f6
Revises: 5d3d97707cf1
Create Date: 2026-03-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "5d3d97707cf1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Use PostgreSQL ENUM for driver_documents.kind."""
    bind = op.get_bind()
    driver_document_kind_enum = sa.Enum(
        "DRIVING_LICENCE",
        "CPC_CERTIFICATE",
        "DIGITAL_TACHOGRAPH",
        "CUSTOM",
        name="driver_document_kind_enum",
    )
    driver_document_kind_enum.create(bind, checkfirst=True)

    op.alter_column(
        "driver_documents",
        "kind",
        existing_type=sa.String(length=40),
        type_=driver_document_kind_enum,
        existing_nullable=False,
        postgresql_using="kind::driver_document_kind_enum",
    )


def downgrade() -> None:
    """Revert driver_documents.kind to VARCHAR."""
    bind = op.get_bind()
    driver_document_kind_enum = sa.Enum(
        "DRIVING_LICENCE",
        "CPC_CERTIFICATE",
        "DIGITAL_TACHOGRAPH",
        "CUSTOM",
        name="driver_document_kind_enum",
    )

    op.alter_column(
        "driver_documents",
        "kind",
        existing_type=driver_document_kind_enum,
        type_=sa.String(length=40),
        existing_nullable=False,
    )
    driver_document_kind_enum.drop(bind, checkfirst=True)
