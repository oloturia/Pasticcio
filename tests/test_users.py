# ============================================================
# tests/test_users.py — tests for user profile endpoint
# ============================================================

import pytest
from httpx import AsyncClient


RECIPE_PAYLOAD = {
    "translation": {
        "language": "en",
        "title": "My Test Recipe",
        "description": "A recipe for testing.",
        "steps": [{"order": 1, "text": "Cook it."}],
    },
    "original_language": "en",
    "ingredients": [],
    "publish": True,
}


# ============================================================
# Profile visibility
# ============================================================

async def test_get_profile_public(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Profile is visible without authentication."""
    response = await client.get("/api/v1/users/testuser")
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "testuser"
    assert "ap_id" in data


async def test_get_profile_fields(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Profile contains expected fields."""
    response = await client.get("/api/v1/users/testuser")
    data = response.json()
    assert "username" in data
    assert "display_name" in data
    assert "bio" in data
    assert "avatar_url" in data
    assert "ap_id" in data
    assert "recipes" in data


async def test_get_profile_not_found(client: AsyncClient):
    """Requesting a nonexistent user returns 404."""
    response = await client.get("/api/v1/users/doesnotexist")
    assert response.status_code == 404


# ============================================================
# Recipes in profile
# ============================================================

async def test_profile_includes_published_recipes(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Published recipes appear in the profile."""
    await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)

    response = await client.get("/api/v1/users/testuser")
    data = response.json()
    assert len(data["recipes"]) >= 1
    assert data["recipes"][0]["title"] == "My Test Recipe"


async def test_profile_excludes_draft_recipes(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Draft recipes are not shown in the public profile."""
    # Create a draft (publish=False)
    await client.post(
        "/api/v1/recipes/",
        json={**RECIPE_PAYLOAD, "publish": False},
        headers=auth_headers,
    )

    response = await client.get("/api/v1/users/testuser")
    recipes = response.json()["recipes"]
    # All returned recipes must be published (they have published_at set)
    for recipe in recipes:
        assert recipe["published_at"] is not None


async def test_profile_recipes_ordered_by_date(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Recipes are returned most recent first."""
    for i in range(3):
        payload = {**RECIPE_PAYLOAD}
        payload["translation"] = {**RECIPE_PAYLOAD["translation"], "title": f"Recipe {i}"}
        await client.post("/api/v1/recipes/", json=payload, headers=auth_headers)

    response = await client.get("/api/v1/users/testuser")
    recipes = response.json()["recipes"]
    assert len(recipes) >= 3
    # published_at should be descending
    dates = [r["published_at"] for r in recipes if r["published_at"]]
    assert dates == sorted(dates, reverse=True)


async def test_profile_recipes_have_required_fields(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Each recipe summary contains the expected fields."""
    await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)

    response = await client.get("/api/v1/users/testuser")
    recipe = response.json()["recipes"][0]
    assert "id" in recipe
    assert "ap_id" in recipe
    assert "slug" in recipe
    assert "title" in recipe
    assert "published_at" in recipe


async def test_profile_max_ten_recipes(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Profile returns at most 10 recipes even if the user has more."""
    for i in range(12):
        payload = {**RECIPE_PAYLOAD}
        payload["translation"] = {**RECIPE_PAYLOAD["translation"], "title": f"Recipe {i}"}
        await client.post("/api/v1/recipes/", json=payload, headers=auth_headers)

    response = await client.get("/api/v1/users/testuser")
    recipes = response.json()["recipes"]
    assert len(recipes) <= 10
