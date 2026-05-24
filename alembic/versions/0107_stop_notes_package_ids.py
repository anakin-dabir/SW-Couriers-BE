"""Add ``stop_notes.package_ids`` JSONB for package-issue delivery notes.

Stores an array of ``packages.id`` UUID strings for ``note_type = PACKAGE_ISSUE_NOTE``
(delivery notes UI: package chips / scoped issue). Nullable; ``ADMIN`` / ``CUSTOMER``
notes keep this column null.

**Revision:** 0107_stop_notes_package_ids  
**Parent:** 0106_org_drafts — apply this revision immediately after 0106.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0107_stop_notes_package_ids"
down_revision: str | None = "0106_org_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stop_notes", sa.Column("package_ids", JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("stop_notes", "package_ids")
