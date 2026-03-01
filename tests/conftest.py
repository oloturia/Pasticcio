# ============================================================
# tests/conftest.py — shared test fixtures
# ============================================================
#
# conftest.py is a special pytest file: fixtures defined here
# are automatically available to ALL test files in the same
# directory and subdirectories — no imports needed.
#
# Our main fixtures:
#   db_session  — a database session that rolls back after each test
#   client      — an HTTP client pointed at the test app
#   test_user   — a ready-made user in the database
#   auth_headers — HTTP headers with a valid JWT for test_user

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.config import settings

# ============================================================
# Test database setup
# ============================================================
#
# We use a SEPARATE database for tests so we never accidentally
# touch development or production data.
#
# The test DATABASE_URL is the same as the regular one but with
# the database name suffixed with "_test".
# e.g. postgresql+asyncpg://pasticcio:pass@db:5432/pasticcio_dev
#   →  postgresql+asyncpg://pasticcio:pass@db:5432/pasticcio_test
#
# IMPORTANT: you must create this database manually once:
#   podman-compose exec db createdb -U pasticcio pasticcio_test

TEST_DATABASE_URL = settings.database_url.replace(
    settings.database_url.rsplit("/", 1)[-1],  # last segment = db name
    settings.database_url.rsplit("/", 1)[-1] + "_test",
)

# Create a separate engine for tests
test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)

TestSessionLocal = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ============================================================
# Session-scoped fixture: create / drop all tables once
# ============================================================
#
# "session" scope means this runs ONCE per pytest run, not once
# per test. Creating and dropping all tables is slow, so we do
# it only at the start and end of the entire test suite.

@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_database():
    """Create all tables before the test suite, drop them after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


# ============================================================
# Function-scoped fixture: one transaction per test
# ============================================================
#
# This is the key trick that keeps tests isolated:
# - Each test gets a fresh database session
# - The session is wrapped in a transaction that is ROLLED BACK
#   at the end of the test
# - This means every test starts with a clean database, without
#   needing to truncate tables or recreate the schema

@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Provide a database session that rolls back after each test.
    Any data written during the test is discarded automatically.
    """
    async with test_engine.connect() as connection:
        # Start a transaction
        await connection.begin()

        # Create a session bound to this connection
        session = AsyncSession(bind=connection, expire_on_commit=False)

        yield session

        # Always roll back — even if the test passed
        await session.close()
        await connection.rollback()


# ============================================================
# HTTP client fixture
# ============================================================
#
# ASGITransport lets httpx call FastAPI directly in memory,
# without opening a real TCP connection. This makes tests fast
# and self-contained.
#
# We override the `get_db` dependency so FastAPI uses the test
# session (the one that rolls back) instead of a real session.

@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client that talks to the app using the test database session."""

    async def override_get_db():
        yield db_session

    # Replace the real database dependency with our test one
    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    # Clean up the override after the test
    app.dependency_overrides.clear()


# ============================================================
# Convenience fixtures: ready-made test data
# ============================================================

@pytest_asyncio.fixture
async def test_user(client: AsyncClient) -> dict:
    """
    Register a user and return their data.
    Uses the API itself — so this also tests registration indirectly.
    """
    response = await client.post("/api/v1/auth/register", json={
        "username": "testuser",
        "email": "test@example.com",
        "password": "TestPassword123!",
        "display_name": "Test User",
    })
    assert response.status_code == 201, f"Failed to create test user: {response.text}"
    return response.json()


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient, test_user: dict) -> dict:
    """
    Log in as test_user and return Authorization headers.
    Use this fixture in tests that require authentication:

        async def test_something(client, auth_headers):
            response = await client.get("/api/v1/...", headers=auth_headers)
    """
    response = await client.post("/api/v1/auth/login", data={
        "username": "testuser",
        "password": "TestPassword123!",
    })
    assert response.status_code == 200, f"Failed to log in test user: {response.text}"
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
