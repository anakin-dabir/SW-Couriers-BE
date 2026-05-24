"""add sequential reference to vehicle_defects

Revision ID: 0124_vehicle_defect_ref
Revises: 0123_audit_log_session_corr_int
Create Date: 2026-05-12

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0124_vehicle_defect_ref"
down_revision: Union[str, None] = "0123_audit_log_session_corr_int"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("CREATE SEQUENCE IF NOT EXISTS defect_ref_seq START 1 INCREMENT 1"))
    op.add_column(
        "vehicle_defects",
        sa.Column(
            "reference",
            sa.String(length=32),
            nullable=True,
        ),
    )
    op.execute(
        sa.text("""
            UPDATE vehicle_defects AS d
            SET reference = 'DF-' || lpad(s.rn::text, 5, '0')
            FROM (
                SELECT id, row_number() OVER (ORDER BY created_at, id) AS rn
                FROM vehicle_defects
            ) AS s
            WHERE d.id = s.id
            """)
    )
    op.alter_column(
        "vehicle_defects",
        "reference",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default=sa.text("'DF-' || lpad(nextval('defect_ref_seq')::text, 5, '0')"),
    )
    op.create_index(op.f("ix_vehicle_defects_reference"), "vehicle_defects", ["reference"], unique=True)
    op.execute(
        sa.text("""
            SELECT CASE
                WHEN EXISTS (SELECT 1 FROM vehicle_defects LIMIT 1) THEN
                    setval(
                        'defect_ref_seq',
                        GREATEST(
                            (SELECT COALESCE(
                                MAX(CAST(NULLIF(SUBSTRING(reference FROM 4), '') AS INTEGER)),
                                0
                            ) FROM vehicle_defects WHERE reference ~ '^DF-[0-9]+$'),
                            1
                        ),
                        true
                    )
                ELSE
                    setval('defect_ref_seq', 1, false)
            END
            """)
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_vehicle_defects_reference"), table_name="vehicle_defects")
    op.drop_column("vehicle_defects", "reference")
    op.execute(sa.text("DROP SEQUENCE IF EXISTS defect_ref_seq"))
