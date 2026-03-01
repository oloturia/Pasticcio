# ============================================================
# tests/test_recipes.py — tests for recipe CRUD endpoints
# ============================================================

import pytest
import uuid
from httpx import AsyncClient

# A minimal valid recipe payload — reused across tests
RECIPE_PAYLOAD = {
    "translation": {
        "language": "en",
        "title": "Simple Pasta",
        "description": "A quick and easy pasta dish.",
        "steps": [
            {"order": 1, "text": "Boil salted water."},
            {"order": 2, "text": "Cook pasta for 8 minutes."},
        ],
    },
    "original_language": "en",
    "ingredients": [
        {"sort_order": 1, "quantity": 200, "unit": "g", "name": "pasta"},
        {"sort_order": 2, "quantity": 2, "unit": "", "name": "garlic cloves"},
    ],
    "dietary_tags": ["vegan"],
    "servings": 2,
}


# ============================================================
# Create recipe tests
# ============================================================

async def test_create_recipe_draft(client: AsyncClient, auth_headers: dict):
    """An authenticated user can create a recipe as a draft."""
    response = await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "draft"
    assert data["translations"][0]["title"] == "Simple Pasta"
    assert "vegan" in data["dietary_tags"]
    assert len(data["ingredients"]) == 2
    assert data["author"]["username"] == "testuser"
    # published_at should be None for drafts
    assert data["published_at"] is None


async def test_create_recipe_published(client: AsyncClient, auth_headers: dict):
    """Setting publish=true creates a published recipe with a published_at timestamp."""
    payload = {**RECIPE_PAYLOAD, "publish": True}
    response = await client.post("/api/v1/recipes/", json=payload, headers=auth_headers)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "published"
    assert data["published_at"] is not None


async def test_create_recipe_unauthenticated(client: AsyncClient):
    """Creating a recipe without a token returns 401."""
    response = await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD)
    assert response.status_code == 401


async def test_create_recipe_generates_slug(client: AsyncClient, auth_headers: dict):
    """The slug is generated from the title."""
    response = await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)
    assert response.status_code == 201
    assert response.json()["slug"] == "simple-pasta"


async def test_create_recipe_duplicate_slug(client: AsyncClient, auth_headers: dict):
    """Two recipes with the same title get different slugs."""
    r1 = await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)
    r2 = await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["slug"] != r2.json()["slug"]


async def test_create_recipe_with_metabolic_tags(client: AsyncClient, auth_headers: dict):
    """Recipes with metabolic tags have show_metabolic_disclaimer=True."""
    payload = {**RECIPE_PAYLOAD, "metabolic_tags": ["low_carb"]}
    response = await client.post("/api/v1/recipes/", json=payload, headers=auth_headers)
    assert response.status_code == 201
    assert response.json()["show_metabolic_disclaimer"] is True


# ============================================================
# Get recipe tests
# ============================================================

async def test_get_recipe(client: AsyncClient, auth_headers: dict):
    """A published recipe can be retrieved by ID."""
    create = await client.post(
        "/api/v1/recipes/", json={**RECIPE_PAYLOAD, "publish": True}, headers=auth_headers
    )
    recipe_id = create.json()["id"]

    response = await client.get(f"/api/v1/recipes/{recipe_id}")
    assert response.status_code == 200
    assert response.json()["id"] == recipe_id


async def test_get_draft_recipe_as_anonymous(client: AsyncClient, auth_headers: dict):
    """A draft recipe is still visible by ID (no auth required to read)."""
    create = await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)
    recipe_id = create.json()["id"]

    response = await client.get(f"/api/v1/recipes/{recipe_id}")
    assert response.status_code == 200


async def test_get_nonexistent_recipe(client: AsyncClient):
    """Getting a recipe that doesn't exist returns 404."""
    response = await client.get(f"/api/v1/recipes/{uuid.uuid4()}")
    assert response.status_code == 404


# ============================================================
# List recipes tests
# ============================================================

async def test_list_recipes_only_published(client: AsyncClient, auth_headers: dict):
    """The list endpoint only returns published recipes."""
    # Create one draft and one published
    await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)
    await client.post("/api/v1/recipes/", json={**RECIPE_PAYLOAD, "publish": True}, headers=auth_headers)

    response = await client.get("/api/v1/recipes/")
    assert response.status_code == 200
    recipes = response.json()
    assert all(r["status"] == "published" for r in recipes)
    assert len(recipes) == 1


async def test_list_recipes_vegan_filter(client: AsyncClient, auth_headers: dict):
    """The vegan filter returns only vegan recipes."""
    # Create a vegan and a non-vegan recipe
    await client.post(
        "/api/v1/recipes/",
        json={**RECIPE_PAYLOAD, "dietary_tags": ["vegan"], "publish": True},
        headers=auth_headers,
    )
    await client.post(
        "/api/v1/recipes/",
        json={**RECIPE_PAYLOAD, "dietary_tags": ["contains_meat"], "publish": True},
        headers=auth_headers,
    )

    response = await client.get("/api/v1/recipes/?vegan=true")
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert "vegan" in results[0]["dietary_tags"]


# ============================================================
# Update recipe tests
# ============================================================

async def test_update_recipe(client: AsyncClient, auth_headers: dict):
    """The author can update their own recipe."""
    create = await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)
    recipe_id = create.json()["id"]

    response = await client.put(
        f"/api/v1/recipes/{recipe_id}",
        json={"servings": 4},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["servings"] == 4


async def test_update_recipe_publish(client: AsyncClient, auth_headers: dict):
    """The author can publish a draft recipe via update."""
    create = await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)
    recipe_id = create.json()["id"]
    assert create.json()["status"] == "draft"

    response = await client.put(
        f"/api/v1/recipes/{recipe_id}",
        json={"publish": True},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "published"


async def test_update_recipe_wrong_user(client: AsyncClient, auth_headers: dict):
    """A user cannot update another user's recipe."""
    create = await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)
    recipe_id = create.json()["id"]

    # Register and log in as a different user
    await client.post("/api/v1/auth/register", json={
        "username": "otheruser",
        "email": "other@example.com",
        "password": "OtherPassword123!",
    })
    login = await client.post("/api/v1/auth/login", data={
        "username": "otheruser",
        "password": "OtherPassword123!",
    })
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.put(
        f"/api/v1/recipes/{recipe_id}",
        json={"servings": 10},
        headers=other_headers,
    )
    assert response.status_code == 403


# ============================================================
# Delete recipe tests
# ============================================================

async def test_delete_recipe(client: AsyncClient, auth_headers: dict):
    """The author can delete their own recipe."""
    create = await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)
    recipe_id = create.json()["id"]

    response = await client.delete(f"/api/v1/recipes/{recipe_id}", headers=auth_headers)
    assert response.status_code == 204

    # The recipe should now return 404
    get_response = await client.get(f"/api/v1/recipes/{recipe_id}")
    assert get_response.status_code == 404


async def test_delete_recipe_wrong_user(client: AsyncClient, auth_headers: dict):
    """A user cannot delete another user's recipe."""
    create = await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)
    recipe_id = create.json()["id"]

    await client.post("/api/v1/auth/register", json={
        "username": "anotheruser",
        "email": "another@example.com",
        "password": "AnotherPassword123!",
    })
    login = await client.post("/api/v1/auth/login", data={
        "username": "anotheruser",
        "password": "AnotherPassword123!",
    })
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.delete(f"/api/v1/recipes/{recipe_id}", headers=other_headers)
    assert response.status_code == 403


async def test_delete_recipe_unauthenticated(client: AsyncClient, auth_headers: dict):
    """Deleting a recipe without a token returns 401."""
    create = await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)
    recipe_id = create.json()["id"]

    response = await client.delete(f"/api/v1/recipes/{recipe_id}")
    assert response.status_code == 401
