"""rename_revoke_reason_to_status_reason

Revision ID: 0067_status_reason
Revises: 0066_doc_access_scope
Create Date: 2026-04-08 01:43:58.101271

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0067_status_reason"
down_revision: Union[str, None] = "0066_doc_access_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_ACTIVITY_TYPE_ENUM = sa.Enum(
    "UPLOADED",
    "DOWNLOADED",
    "VIEWED",
    "SHARED",
    "EXPIRED",
    "DELETED",
    name="orgdocumentactivitytype",
    native_enum=False,
)

NEW_ACTIVITY_TYPE_ENUM = sa.Enum(
    "UPLOADED",
    "DOWNLOADED",
    "VIEWED",
    "SHARED",
    "EXPIRED",
    "DELETED",
    "REVOKED",
    "EXTENDED",
    name="orgdocumentactivitytype",
    native_enum=False,
)


def upgrade() -> None:
    op.alter_column(
        "org_document_shares",
        "revoke_reason",
        new_column_name="status_reason",
        existing_type=sa.String(length=500),
        existing_nullable=True,
    )

    op.alter_column(
        "org_document_activities",
        "activity_type",
        existing_type=OLD_ACTIVITY_TYPE_ENUM,
        type_=NEW_ACTIVITY_TYPE_ENUM,
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE org_document_activities
        SET activity_type = 'DELETED'
        WHERE activity_type IN ('REVOKED', 'EXTENDED')
        """
    )

    op.alter_column(
        "org_document_activities",
        "activity_type",
        existing_type=NEW_ACTIVITY_TYPE_ENUM,
        type_=OLD_ACTIVITY_TYPE_ENUM,
        existing_nullable=False,
    )

    op.alter_column(
        "org_document_shares",
        "status_reason",
        new_column_name="revoke_reason",
        existing_type=sa.String(length=500),
        existing_nullable=True,
    )