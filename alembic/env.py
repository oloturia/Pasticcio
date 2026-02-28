# ============================================================
# alembic/env.py — Alembic environment configuration
# ============================================================
#
# This file is executed by Alembic every time you run a migration
# command. It's responsible for:
#   1. Connecting to the database
#   2. Telling Alembic about our models (so it can detect changes)
#   3. Running migrations in async mode (since we use asyncpg)
#
# You rarely need to edit this file unless you're adding a new
# database or changing how connections work.

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# --- Import our models ---
# This is the key import: by importing Base and all models,
# Alembic knows the full target schema and can compare it
# against the actual database to generate migrations.
from app.database import Base
import app.models  # noqa: F401 — side effect import, loads all models

from app.config import settings

# Alembic Config object — gives access to alembic.ini values
config = context.config

# Set up Python logging from the alembic.ini [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the database URL with our environment variable.
# This way we never hardcode credentials in alembic.ini.
# Note: asyncpg uses postgresql+asyncpg://, but Alembic's sync
# runner needs postgresql+psycopg2:// — we swap the driver here.
config.set_main_option(
    "sqlalchemy.url",
    settings.database_url.replace("postgresql+asyncpg", "postgresql+psycopg2"),
)

# The metadata object Alembic uses to detect schema changes.
# It must include ALL models — that's why we imported app.models above.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    In offline mode, Alembic generates SQL scripts instead of
    executing them directly. Useful for reviewing migrations
    before applying them, or for databases you can't connect to
    directly (e.g. a managed cloud database behind a firewall).

    Usage: alembic upgrade head --sql > migration.sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Compare server defaults (e.g. func.now()) so Alembic
        # doesn't think columns with defaults have changed
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_server_default=True,
        # Detect column type changes (e.g. String → Text)
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Run migrations in 'online' mode with an async engine.

    Online mode connects to the database and runs migrations
    directly. This is what happens when you run `alembic upgrade head`.
    """
    # We need a sync-compatible engine for Alembic even though
    # our app uses async. We use psycopg2 here (sync driver)
    # instead of asyncpg (async driver) just for migrations.
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # don't pool connections during migrations
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
