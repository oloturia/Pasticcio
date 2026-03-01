# ============================================================
# tests/conftest.py — shared test fixtures
# ============================================================

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import Base, get_db
from app.main import app
from app.config import settings

# ============================================================
# Test database
# ============================================================
# Separate database for tests — never touches dev/prod data.
# Create it once manually:
#   podman-compose exec db createdb -U pasticcio pasticcio_dev_test

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/pasticcio_dev_test"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)

TestSessionLocal = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Tables to truncate between tests, in the right order
# (children before parents to respect foreign keys)
TABLES_TO_TRUNCATE = [
    "recipe_photos",
    "recipe_ingredients",
    "recipe_translations",
    "recipes",
    "food_items",
    "users",
]


# ============================================================
# Session-scoped: create tables once for the whole test run
# ============================================================

@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_database():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ============================================================
# Function-scoped: clean tables before each test
# ============================================================

@pytest_asyncio.fixture(autouse=True)
async def clean_tables():
    """Truncate all tables before each test for a clean slate."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    async with engine.begin() as conn:
        for table in TABLES_TO_TRUNCATE:
            await conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
    await engine.dispose()
    yield

# ============================================================
# Database session fixture
# ============================================================

@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a plain async database session for the test."""
    async with TestSessionLocal() as session:
        yield session


# ============================================================
# HTTP client fixture
# ============================================================

@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """HTTP client wired to the test database."""

    async def override_get_db():
        engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)		
        async with async_session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        await engine.dispose()
    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


# ============================================================
# Convenience fixtures
# ============================================================

@pytest_asyncio.fixture
async def test_user(client: AsyncClient) -> dict:
    """Register a test user and return their data."""
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
    """Log in as test_user and return Authorization headers."""
    response = await client.post("/api/v1/auth/login", data={
        "username": "testuser",
        "password": "TestPassword123!",
    })
    assert response.status_code == 200, f"Failed to log in: {response.text}"
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
