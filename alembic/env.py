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
from sqlalchemy import engine_from_config, pool

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
# Alembic uses psycopg2 (sync driver) for migrations, so we
# swap asyncpg → psycopg2 in the URL.
sync_url = settings.database_url.replace("postgresql+asyncpg", "postgresql+psycopg2")
config.set_main_option("sqlalchemy.url", sync_url)

# The metadata object Alembic uses to detect schema changes.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode — generate SQL without
    connecting to the database. Useful for review or air-gapped servers.

    Usage: alembic upgrade head --sql > migration.sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode — connect and apply directly.
    This is what happens when you run `alembic upgrade head`.
    Uses a plain sync engine (psycopg2), not the async one.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_server_default=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
