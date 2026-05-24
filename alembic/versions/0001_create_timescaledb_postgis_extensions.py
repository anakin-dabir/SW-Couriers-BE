"""Create TimescaleDB and PostGIS extensions.

Revision ID: 0001_ext
Revises:
Create Date: (run first before any table migrations)

Per architecture: TimescaleDB and PostGIS must be installed at server level first.
"""

import sys
from collections.abc import Sequence

import sqlalchemy.exc

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_ext"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXTENSION_INSTALL_MSG = """
TimescaleDB and PostGIS must be installed on the system where PostgreSQL runs before running migrations.

Install them first, then run: ./scripts/migrate upgrade head

  • TimescaleDB: https://docs.timescale.com/install/
  • PostGIS:     https://postgis.net/install/

On Arch Linux: sudo pacman -S postgis  and  yay -S timescaledb  (or paru)
"""


def upgrade() -> None:
    """Create TimescaleDB and PostGIS extensions. Use literal SQL only (no interpolation)."""
    try:
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    except sqlalchemy.exc.NotSupportedError:
        print(_EXTENSION_INSTALL_MSG.strip(), file=sys.stderr)
        sys.exit(1)
    try:
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    except sqlalchemy.exc.NotSupportedError:
        print(_EXTENSION_INSTALL_MSG.strip(), file=sys.stderr)
        sys.exit(1)


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS postgis")
    op.execute("DROP EXTENSION IF EXISTS timescaledb")
