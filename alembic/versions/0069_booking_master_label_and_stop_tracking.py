"""booking_master_label_and_stop_tracking

Revision ID: 0069_master_label_stop_track
Revises: 0068_vi_defect_fk
Create Date: 2026-04-09 18:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0069_master_label_stop_track"
down_revision: Union[str, None] = "0068_vi_defect_fk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Booking-level parent label for all delivery stops.
    op.add_column("bookings", sa.Column("master_label_id", sa.String(length=40), nullable=True))
    op.create_index(op.f("ix_bookings_master_label_id"), "bookings", ["master_label_id"], unique=True)

    # Stop-level customer-visible tracking id.
    op.add_column("delivery_stops", sa.Column("tracking_id", sa.String(length=40), nullable=True))
    op.create_index(op.f("ix_delivery_stops_tracking_id"), "delivery_stops", ["tracking_id"], unique=True)

    # Transition package-level tracking to optional during compatibility window.
    op.alter_column(
        "packages",
        "tracking_id",
        existing_type=sa.String(length=30),
        nullable=True,
    )

    # Backfill booking master labels.
    op.execute(
        """
        UPDATE bookings
        SET master_label_id = CONCAT('ML-', UPPER(SUBSTRING(REPLACE(id::text, '-', '') FROM 1 FOR 12)))
        WHERE master_label_id IS NULL
        """
    )

    # Backfill delivery stop tracking from oldest package tracking within each stop.
    op.execute(
        """
        WITH ranked AS (
            SELECT
                p.delivery_stop_id,
                p.tracking_id,
                ROW_NUMBER() OVER (
                    PARTITION BY p.delivery_stop_id
                    ORDER BY p.created_at ASC NULLS LAST, p.id ASC
                ) AS rn
            FROM packages p
            WHERE p.delivery_stop_id IS NOT NULL
              AND p.tracking_id IS NOT NULL
        )
        UPDATE delivery_stops ds
        SET tracking_id = ranked.tracking_id
        FROM ranked
        WHERE ranked.delivery_stop_id = ds.id
          AND ranked.rn = 1
          AND ds.tracking_id IS NULL
        """
    )

    # Fill remaining stops (e.g. no packages yet) with generated stop tracking ids.
    op.execute(
        """
        UPDATE delivery_stops
        SET tracking_id = CONCAT('TRK-', UPPER(SUBSTRING(REPLACE(id::text, '-', '') FROM 1 FOR 12)))
        WHERE tracking_id IS NULL
        """
    )


def downgrade() -> None:
    # Restore package tracking non-null requirement.
    op.alter_column(
        "packages",
        "tracking_id",
        existing_type=sa.String(length=30),
        nullable=False,
    )

    op.drop_index(op.f("ix_delivery_stops_tracking_id"), table_name="delivery_stops")
    op.drop_column("delivery_stops", "tracking_id")

    op.drop_index(op.f("ix_bookings_master_label_id"), table_name="bookings")
    op.drop_column("bookings", "master_label_id")
