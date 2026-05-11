# ============================================================
# tests/test_moderation.py — tests for blocks, mutes, bookmarks, admin
# ============================================================

import uuid
import pytest
from httpx import AsyncClient


# ============================================================
# Helpers
# ============================================================

async def _register_and_login(client, username, email, password):
    await client.post("/api/v1/auth/register", json={
        "username": username,
        "email": email,
        "password": password,
    })
    r = await client.post("/api/v1/auth/login", data={
        "username": username,
        "password": password,
    })
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


REMOTE_AP_ID = "https://remote.example.com/users/someuser"


# ============================================================
# Block tests
# ============================================================

async def test_block_remote_user(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """User can block a remote user by AP ID."""
    response = await client.post(
        f"/api/v1/users/{REMOTE_AP_ID}/block",
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert response.json()["status"] == "blocked"


async def test_block_idempotent(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Blocking the same user twice does not raise an error."""
    await client.post(f"/api/v1/users/{REMOTE_AP_ID}/block", headers=auth_headers)
    r = await client.post(f"/api/v1/users/{REMOTE_AP_ID}/block", headers=auth_headers)
    assert r.status_code == 201


async def test_cannot_block_yourself(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Cannot block your own AP ID."""
    own_ap_id = test_user["ap_id"]
    r = await client.post(f"/api/v1/users/{own_ap_id}/block", headers=auth_headers)
    assert r.status_code == 400


async def test_unblock_user(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """User can unblock a previously blocked user."""
    await client.post(f"/api/v1/users/{REMOTE_AP_ID}/block", headers=auth_headers)
    r = await client.delete(f"/api/v1/users/{REMOTE_AP_ID}/block", headers=auth_headers)
    assert r.status_code == 204


async def test_list_blocks(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """List blocks returns all blocked users."""
    await client.post(f"/api/v1/users/{REMOTE_AP_ID}/block", headers=auth_headers)
    r = await client.get("/api/v1/blocks", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["blocked_ap_id"] == REMOTE_AP_ID
    assert r.json()[0]["block_type"] == "block"


async def test_blocks_require_auth(client: AsyncClient):
    """Block endpoints require authentication."""
    r = await client.post(f"/api/v1/users/{REMOTE_AP_ID}/block")
    assert r.status_code == 401


# ============================================================
# Mute tests
# ============================================================

async def test_mute_remote_user(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """User can mute a remote user."""
    r = await client.post(f"/api/v1/users/{REMOTE_AP_ID}/mute", headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["status"] == "muted"


async def test_unmute_user(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """User can unmute a previously muted user."""
    await client.post(f"/api/v1/users/{REMOTE_AP_ID}/mute", headers=auth_headers)
    r = await client.delete(f"/api/v1/users/{REMOTE_AP_ID}/mute", headers=auth_headers)
    assert r.status_code == 204


async def test_list_mutes(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """List mutes returns all muted users."""
    await client.post(f"/api/v1/users/{REMOTE_AP_ID}/mute", headers=auth_headers)
    r = await client.get("/api/v1/mutes", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["block_type"] == "mute"


async def test_block_and_mute_are_separate(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Block and mute are stored separately."""
    other_ap_id = "https://remote.example.com/users/other"
    await client.post(f"/api/v1/users/{REMOTE_AP_ID}/block", headers=auth_headers)
    await client.post(f"/api/v1/users/{other_ap_id}/mute", headers=auth_headers)

    blocks = await client.get("/api/v1/blocks", headers=auth_headers)
    mutes = await client.get("/api/v1/mutes", headers=auth_headers)

    assert len(blocks.json()) == 1
    assert len(mutes.json()) == 1
    assert blocks.json()[0]["blocked_ap_id"] == REMOTE_AP_ID
    assert mutes.json()[0]["blocked_ap_id"] == other_ap_id


# ============================================================
# Bookmark tests
# ============================================================

RECIPE_AP_ID = "https://pasticcio.localhost/users/testuser/recipes/pasta"

async def test_add_bookmark(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """User can bookmark a recipe."""
    r = await client.post("/api/v1/bookmarks", json={
        "recipe_ap_id": RECIPE_AP_ID,
        "title": "My Pasta",
        "author_ap_id": "https://pasticcio.localhost/users/testuser",
        "author_name": "Test User",
    }, headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["recipe_ap_id"] == RECIPE_AP_ID
    assert r.json()["title"] == "My Pasta"


async def test_bookmark_idempotent(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Bookmarking the same recipe twice returns the existing bookmark."""
    payload = {"recipe_ap_id": RECIPE_AP_ID, "title": "Pasta"}
    r1 = await client.post("/api/v1/bookmarks", json=payload, headers=auth_headers)
    r2 = await client.post("/api/v1/bookmarks", json=payload, headers=auth_headers)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]


async def test_remove_bookmark(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """User can remove a bookmark."""
    r = await client.post("/api/v1/bookmarks", json={
        "recipe_ap_id": RECIPE_AP_ID,
    }, headers=auth_headers)
    bookmark_id = r.json()["id"]

    del_r = await client.delete(f"/api/v1/bookmarks/{bookmark_id}", headers=auth_headers)
    assert del_r.status_code == 204

    bookmarks = await client.get("/api/v1/bookmarks", headers=auth_headers)
    assert len(bookmarks.json()) == 0


async def test_list_bookmarks(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """List bookmarks returns all saved recipes."""
    await client.post("/api/v1/bookmarks", json={"recipe_ap_id": RECIPE_AP_ID}, headers=auth_headers)
    await client.post("/api/v1/bookmarks", json={"recipe_ap_id": "https://other.example.com/recipe/1"}, headers=auth_headers)

    r = await client.get("/api/v1/bookmarks", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_bookmarks_require_auth(client: AsyncClient):
    """Bookmark endpoints require authentication."""
    r = await client.get("/api/v1/bookmarks")
    assert r.status_code == 401


async def test_remove_other_user_bookmark_returns_404(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Cannot remove another user's bookmark."""
    r = await client.post("/api/v1/bookmarks", json={"recipe_ap_id": RECIPE_AP_ID}, headers=auth_headers)
    bookmark_id = r.json()["id"]

    other_headers = await _register_and_login(
        client, "otheruser", "other@example.com", "OtherPass123!"
    )
    del_r = await client.delete(f"/api/v1/bookmarks/{bookmark_id}", headers=other_headers)
    assert del_r.status_code == 404


# ============================================================
# Admin tests
# ============================================================

async def _make_admin(client, auth_headers):
    """Helper — directly set is_admin via API is not possible, so we test
    by checking the 403 when not admin."""
    pass


async def test_admin_endpoints_require_admin(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Admin endpoints return 403 for non-admin users."""
    r = await client.get("/api/v1/admin/instances", headers=auth_headers)
    assert r.status_code == 403

    r = await client.post("/api/v1/admin/instances", json={
        "domain": "spam.example.com",
        "rule_type": "block",
    }, headers=auth_headers)
    assert r.status_code == 403

    r = await client.post(
        f"/api/v1/admin/users/{uuid.uuid4()}/ban",
        headers=auth_headers,
    )
    assert r.status_code == 403


async def test_admin_endpoints_require_auth(client: AsyncClient):
    """Admin endpoints return 401 without authentication."""
    r = await client.get("/api/v1/admin/instances")
    assert r.status_code == 401
