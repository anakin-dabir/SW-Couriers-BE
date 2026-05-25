"""Alembic migration environment. Uses asyncpg via async engine; discovers models via Base.metadata.

IMPORTANT: Every new model module must be imported here for autogenerate to detect it.

Migration policy:
- Never edit revisions already applied to production/staging; add a new revision instead.
- For data migrations with dynamic values, use ``alembic.sql_helpers`` (bindparams), not f-strings.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# ── Import ALL models so Base.metadata sees them ─────────────────
import app.models  # noqa: F401
from alembic import context
from app.common.models import Base
from app.core.config import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
target_metadata = Base.metadata

# ── Exclude PostGIS system tables from autogenerate ──────────────
_EXCLUDE_TABLES: frozenset[str] = frozenset({"spatial_ref_sys"})


def _include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001, ARG001
    """Exclude PostGIS system tables from autogenerate detection."""
    return not (type_ == "table" and name in _EXCLUDE_TABLES)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode: only generate SQL, no DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=_include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:  # noqa: ANN001
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations using asyncpg (no psycopg2 required)."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
