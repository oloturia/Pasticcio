# ============================================================
# tests/test_auth.py — tests for registration, login, and /me
# ============================================================
#
# Each test function:
#   1. Sets up its scenario (maybe creates a user first)
#   2. Calls the API via `client`
#   3. Asserts the response is what we expect
#
# The `client` and `db_session` fixtures from conftest.py are
# injected automatically by pytest — no imports needed.

import pytest
from httpx import AsyncClient


# ============================================================
# Registration tests
# ============================================================

async def test_register_success(client: AsyncClient):
    """A new user can register with valid data."""
    response = await client.post("/api/v1/auth/register", json={
        "username": "maria",
        "email": "maria@example.com",
        "password": "SecurePass123!",
        "display_name": "Maria Rossi",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "maria"
    assert data["email"] == "maria@example.com"
    assert data["display_name"] == "Maria Rossi"
    # The AP id should be constructed from the instance domain and username
    assert "maria" in data["ap_id"]
    # Password must never appear in the response
    assert "password" not in data
    assert "hashed_password" not in data


async def test_register_duplicate_username(client: AsyncClient):
    """Registering with an already-taken username returns 409."""
    payload = {
        "username": "luigi",
        "email": "luigi@example.com",
        "password": "SecurePass123!",
    }
    # First registration — should succeed
    r1 = await client.post("/api/v1/auth/register", json=payload)
    assert r1.status_code == 201

    # Second registration with same username — should fail
    r2 = await client.post("/api/v1/auth/register", json={
        **payload,
        "email": "luigi2@example.com",  # different email, same username
    })
    assert r2.status_code == 409


async def test_register_duplicate_email(client: AsyncClient):
    """Registering with an already-taken email returns 409."""
    await client.post("/api/v1/auth/register", json={
        "username": "anna",
        "email": "anna@example.com",
        "password": "SecurePass123!",
    })
    response = await client.post("/api/v1/auth/register", json={
        "username": "anna2",
        "email": "anna@example.com",  # same email
        "password": "SecurePass123!",
    })
    assert response.status_code == 409


async def test_register_username_too_short(client: AsyncClient):
    """Usernames shorter than 3 characters are rejected."""
    response = await client.post("/api/v1/auth/register", json={
        "username": "ab",
        "email": "ab@example.com",
        "password": "SecurePass123!",
    })
    assert response.status_code == 422  # Unprocessable Entity — validation error


async def test_register_invalid_username_characters(client: AsyncClient):
    """Usernames with spaces or special characters are rejected."""
    response = await client.post("/api/v1/auth/register", json={
        "username": "mario rossi",  # space not allowed
        "email": "mario@example.com",
        "password": "SecurePass123!",
    })
    assert response.status_code == 422


async def test_register_password_too_short(client: AsyncClient):
    """Passwords shorter than 8 characters are rejected."""
    response = await client.post("/api/v1/auth/register", json={
        "username": "sofia",
        "email": "sofia@example.com",
        "password": "short",
    })
    assert response.status_code == 422


async def test_register_invalid_email(client: AsyncClient):
    """Invalid email addresses are rejected."""
    response = await client.post("/api/v1/auth/register", json={
        "username": "giuseppe",
        "email": "not-an-email",
        "password": "SecurePass123!",
    })
    assert response.status_code == 422


# ============================================================
# Login tests
# ============================================================

async def test_login_success(client: AsyncClient, test_user: dict):
    """A registered user can log in and receive a JWT token."""
    response = await client.post("/api/v1/auth/login", data={
        "username": "testuser",
        "password": "TestPassword123!",
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    # Token should be a non-empty string
    assert isinstance(data["access_token"], str)
    assert len(data["access_token"]) > 0


async def test_login_wrong_password(client: AsyncClient, test_user: dict):
    """Login with wrong password returns 401."""
    response = await client.post("/api/v1/auth/login", data={
        "username": "testuser",
        "password": "WrongPassword!",
    })
    assert response.status_code == 401


async def test_login_nonexistent_user(client: AsyncClient):
    """Login with a username that doesn't exist returns 401."""
    response = await client.post("/api/v1/auth/login", data={
        "username": "nobody",
        "password": "SomePassword123!",
    })
    assert response.status_code == 401


async def test_login_wrong_password_same_status_as_wrong_user(client: AsyncClient, test_user: dict):
    """
    Wrong password and wrong username return the same status code.
    This prevents attackers from enumerating valid usernames by
    comparing response codes or timing.
    """
    wrong_password = await client.post("/api/v1/auth/login", data={
        "username": "testuser",
        "password": "WrongPassword!",
    })
    wrong_user = await client.post("/api/v1/auth/login", data={
        "username": "nobody",
        "password": "WrongPassword!",
    })
    assert wrong_password.status_code == wrong_user.status_code == 401


# ============================================================
# /me tests
# ============================================================

async def test_me_authenticated(client: AsyncClient, auth_headers: dict):
    """/me returns the current user's profile when authenticated."""
    response = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "testuser"
    assert "hashed_password" not in data


async def test_me_unauthenticated(client: AsyncClient):
    """/me returns 401 when no token is provided."""
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


async def test_me_invalid_token(client: AsyncClient):
    """/me returns 401 when an invalid token is provided."""
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer this.is.not.a.valid.token"},
    )
    assert response.status_code == 401
