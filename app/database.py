# ============================================================
# app/database.py — database connection and session management
# ============================================================
#
# This file sets up the async connection to PostgreSQL via
# SQLAlchemy. Every part of the app that needs to read or write
# data imports `get_db` and uses it as a FastAPI dependency.

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# --- Engine ---
# The engine is the low-level connection to PostgreSQL.
# It manages a pool of connections so we don't open a new
# TCP connection for every single request.
# echo=True prints all SQL queries to the console — useful
# in development, should be False in production.
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    # Max number of connections kept open in the pool.
    # For a Raspberry Pi, keep this low to save RAM.
    pool_size=5,
    max_overflow=10,
)

# --- Session factory ---
# AsyncSession is the object we use to run queries.
# async_sessionmaker creates new sessions on demand.
# expire_on_commit=False means we can still access object
# attributes after committing a transaction.
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# --- Declarative base ---
# All SQLAlchemy models inherit from this class.
# It keeps track of all defined tables so Alembic can
# compare them against the actual database schema.
class Base(DeclarativeBase):
    pass


# --- FastAPI dependency ---
# Used in route handlers with `db: AsyncSession = Depends(get_db)`.
# The `async with` block ensures the session is always closed
# after the request, even if an exception occurs.
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
