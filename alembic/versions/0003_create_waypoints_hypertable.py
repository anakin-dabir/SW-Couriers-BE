"""Convert waypoints to TimescaleDB hypertable.

Revision ID: 0003_hyper
Revises: da8153402035
Create Date: 2026-02-21

Per architecture: waypoints are partitioned by recorded_at for time-series
queries, automatic compression (after 7 days), and retention policies.

TimescaleDB requires the partitioning column to be in the primary key,
so we change PK from (id) to (id, recorded_at).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_hyper"
down_revision: str = "da8153402035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Convert waypoints table to a TimescaleDB hypertable partitioned by recorded_at."""
    # Step 1: Change PK to composite (id, recorded_at) — required by TimescaleDB
    op.execute("ALTER TABLE waypoints DROP CONSTRAINT waypoints_pkey")
    op.execute("ALTER TABLE waypoints ADD PRIMARY KEY (id, recorded_at)")

    # Step 2: Convert to hypertable — chunk_time_interval = 1 day for high-freq GPS telemetry
    op.execute("SELECT create_hypertable('waypoints', 'recorded_at', " "chunk_time_interval => INTERVAL '1 day', " "migrate_data => true)")

    # Step 3: Enable compression after 7 days (old GPS data is rarely queried hot)
    op.execute("ALTER TABLE waypoints SET (" "timescaledb.compress, " "timescaledb.compress_segmentby = 'driver_id', " "timescaledb.compress_orderby = 'recorded_at DESC')")

    # Step 4: Compression policy — automatically compress chunks older than 7 days
    op.execute("SELECT add_compression_policy('waypoints', INTERVAL '7 days')")

    # Step 5: Retention policy — drop raw GPS data older than 90 days (GDPR compliance)
    op.execute("SELECT add_retention_policy('waypoints', INTERVAL '90 days')")


def downgrade() -> None:
    """Remove TimescaleDB policies. Full revert requires drop + recreate."""
    op.execute("SELECT remove_retention_policy('waypoints', if_exists => true)")
    op.execute("SELECT remove_compression_policy('waypoints', if_exists => true)")
    # Decompress all chunks before any structural changes
    op.execute("SELECT decompress_chunk(c, if_compressed => true) " "FROM show_chunks('waypoints') c")
    # Note: There is no direct "undo hypertable" in TimescaleDB.
    # For dev, `alembic downgrade base && alembic upgrade head` is the cleanest approach.
