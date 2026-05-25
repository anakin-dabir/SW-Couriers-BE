"""Safe SQL helpers for Alembic data migrations.

Use these in **new** migrations only. Never rewrite migrations that have already
been applied to production — Alembic records revision IDs, not file checksums, but
editing applied files causes drift between prod history, fresh installs, and
downgrade paths.

Guidelines:
- Bind **values** with ``sa.text(...).bindparams(...)`` — never f-string user/row data.
- Table/column identifiers must come from a hardcoded allowlist in the migration file.
- ``server_default`` / DDL literals cannot use runtime bindparams; use module constants.
"""

from __future__ import annotations

import re
from typing import Any

import sqlalchemy as sa
from alembic import op

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _validate_ident(name: str, *, kind: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"Invalid SQL {kind} identifier: {name!r}")
    return name


def execute_bound(statement: str, /, **params: Any) -> None:
    """Run parametrized DML via ``op.execute(sa.text(...).bindparams(...))``."""
    op.execute(sa.text(statement).bindparams(**params))


def update_uuid_column(
    table: str,
    column: str,
    value: str,
    *,
    where: dict[str, str] | None = None,
) -> None:
    """Set a UUID column using bound parameters.

    Example::

        update_uuid_column(
            "qb_connections",
            "organization_id",
            namespace_id,
            where={"organization_id": old_namespace_id},
        )
    """
    table = _validate_ident(table, kind="table")
    column = _validate_ident(column, kind="column")

    if where:
        where_clauses = " AND ".join(
            f"{_validate_ident(col, kind='column')} = CAST(:where_{col} AS uuid)" for col in where
        )
        statement = f"UPDATE {table} SET {column} = CAST(:value AS uuid) WHERE {where_clauses}"
        bind: dict[str, str] = {"value": value}
        bind.update({f"where_{col}": val for col, val in where.items()})
        execute_bound(statement, **bind)
    else:
        execute_bound(
            f"UPDATE {table} SET {column} = CAST(:value AS uuid)",
            value=value,
        )


def uuid_server_default(uuid_literal: str) -> sa.TextClause:
    """Build a static ``server_default`` for a UUID column (DDL — no bindparams)."""
    return sa.text("'" + uuid_literal + "'::uuid")
