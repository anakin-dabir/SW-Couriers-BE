"""convert organizations.company_size from varchar to enum

Aligns the `organizations.company_size` column with the model definition:
a non-native `CompanySize` enum with the six employee-range labels as values.

Existing rows were stored as enum key names (e.g. `EMPLOYEES_1_10`) by an
earlier migration; this migration rewrites them to the canonical values
(`1-10 employees`, ...) before swapping the column type to
`sa.Enum(..., native_enum=False)` so the generated CHECK constraint accepts
the data. The downgrade mirrors both steps.

Revision ID: 0081_orgs_company_size_enum
Revises: 0080_credit_modules_initial
Create Date: 2026-04-22

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0081_orgs_company_size_enum"
down_revision: str | None = "0080_credit_modules_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COMPANY_SIZE_VALUES = (
    "1-10 employees",
    "11-50 employees",
    "51-200 employees",
    "201-500 employees",
    "501-1000 employees",
    "1000+ employees",
)

_KEY_TO_VALUE: tuple[tuple[str, str], ...] = (
    ("EMPLOYEES_1_10", "1-10 employees"),
    ("EMPLOYEES_11_50", "11-50 employees"),
    ("EMPLOYEES_51_200", "51-200 employees"),
    ("EMPLOYEES_201_500", "201-500 employees"),
    ("EMPLOYEES_501_1000", "501-1000 employees"),
    ("EMPLOYEES_1000_PLUS", "1000+ employees"),
)


def upgrade() -> None:
    for key, value in _KEY_TO_VALUE:
        op.execute(
            sa.text("UPDATE organizations SET company_size = :new WHERE company_size = :old").bindparams(
                new=value, old=key,
            )
        )

    op.alter_column(
        "organizations",
        "company_size",
        existing_type=sa.VARCHAR(length=19),
        type_=sa.Enum(*_COMPANY_SIZE_VALUES, name="companysize", native_enum=False),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "organizations",
        "company_size",
        existing_type=sa.Enum(*_COMPANY_SIZE_VALUES, name="companysize", native_enum=False),
        type_=sa.VARCHAR(length=19),
        existing_nullable=False,
    )

    for key, value in _KEY_TO_VALUE:
        op.execute(
            sa.text("UPDATE organizations SET company_size = :old WHERE company_size = :new").bindparams(
                new=value, old=key,
            )
        )
