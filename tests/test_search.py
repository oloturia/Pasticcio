# ============================================================
# tests/test_search.py — tests for recipe search endpoint
# ============================================================

import pytest
from httpx import AsyncClient

RECIPE_VEGAN_PASTA = {
    "translation": {
        "language": "en",
        "title": "Vegan Pasta Primavera",
        "description": "A light spring pasta with vegetables.",
        "steps": [],
    },
    "original_language": "en",
    "ingredients": [
        {"sort_order": 1, "name": "pasta", "quantity": 200, "unit": "g"},
        {"sort_order": 2, "name": "zucchini", "quantity": 1, "unit": "piece"},
        {"sort_order": 3, "name": "carrot", "quantity": 2, "unit": "piece"},
    ],
    "dietary_tags": ["vegan"],
    "publish": True,
}

RECIPE_MEAT_STEW = {
    "translation": {
        "language": "en",
        "title": "Beef Stew with Tomatoes",
        "description": "A hearty winter stew.",
        "steps": [],
    },
    "original_language": "en",
    "ingredients": [
        {"sort_order": 1, "name": "beef", "quantity": 500, "unit": "g"},
        {"sort_order": 2, "name": "tomato", "quantity": 3, "unit": "piece"},
        {"sort_order": 3, "name": "onion", "quantity": 1, "unit": "piece"},
    ],
    "dietary_tags": ["contains_meat"],
    "publish": True,
}

RECIPE_ITALIAN_SOUP = {
    "translation": {
        "language": "it",
        "title": "Minestrone",
        "description": "Zuppa di verdure italiana.",
        "steps": [],
    },
    "original_language": "it",
    "ingredients": [
        {"sort_order": 1, "name": "carrot", "quantity": 2, "unit": "piece"},
        {"sort_order": 2, "name": "onion", "quantity": 1, "unit": "piece"},
        {"sort_order": 3, "name": "zucchini", "quantity": 1, "unit": "piece"},
    ],
    "dietary_tags": ["vegan"],
    "publish": True,
}


async def _create_recipes(client, auth_headers):
    """Create all test recipes."""
    results = []
    for payload in [RECIPE_VEGAN_PASTA, RECIPE_MEAT_STEW, RECIPE_ITALIAN_SOUP]:
        r = await client.post("/api/v1/recipes/", json=payload, headers=auth_headers)
        assert r.status_code == 201
        results.append(r.json())
    return results


# ============================================================
# Basic search tests
# ============================================================

async def test_search_returns_only_published(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Search returns only published recipes."""
    await _create_recipes(client, auth_headers)
    # Create a draft
    await client.post("/api/v1/recipes/", json={
        **RECIPE_VEGAN_PASTA,
        "publish": False,
        "translation": {**RECIPE_VEGAN_PASTA["translation"], "title": "Draft Recipe"}
    }, headers=auth_headers)

    r = await client.get("/api/v1/search/")
    assert r.status_code == 200
    titles = [res["translations"][0]["title"] for res in r.json()]
    assert "Draft Recipe" not in titles


async def test_search_all_returns_all_published(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Search without filters returns all published recipes."""
    await _create_recipes(client, auth_headers)
    r = await client.get("/api/v1/search/")
    assert r.status_code == 200
    assert len(r.json()) == 3


# ============================================================
# Full-text search tests
# ============================================================

async def test_search_by_title(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Full-text search finds recipes by title."""
    await _create_recipes(client, auth_headers)
    r = await client.get("/api/v1/search/?q=pasta")
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 1
    assert "Pasta" in results[0]["translations"][0]["title"]


async def test_search_by_description(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Full-text search finds recipes by description."""
    await _create_recipes(client, auth_headers)
    r = await client.get("/api/v1/search/?q=hearty")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert "Stew" in r.json()[0]["translations"][0]["title"]


async def test_search_no_results(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Search with no matches returns empty list."""
    await _create_recipes(client, auth_headers)
    r = await client.get("/api/v1/search/?q=thisrecipedoesnotexist")
    assert r.status_code == 200
    assert r.json() == []


# ============================================================
# Tag filter tests
# ============================================================

async def test_search_by_tag(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Tag filter returns only recipes with that tag."""
    await _create_recipes(client, auth_headers)
    r = await client.get("/api/v1/search/?tags=vegan")
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 2
    for result in results:
        assert "vegan" in result["dietary_tags"]


async def test_search_by_multiple_tags(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Multiple tags are ANDed together."""
    await _create_recipes(client, auth_headers)
    r = await client.get("/api/v1/search/?tags=vegan,contains_meat")
    # No recipe can be both vegan and contains_meat
    assert r.status_code == 200
    assert len(r.json()) == 0


# ============================================================
# Language filter tests
# ============================================================

async def test_search_by_language(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Language filter returns only recipes in that language."""
    await _create_recipes(client, auth_headers)
    r = await client.get("/api/v1/search/?language=it")
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 1
    assert results[0]["original_language"] == "it"


# ============================================================
# Ingredient search tests
# ============================================================

async def test_search_by_ingredient(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Ingredient filter returns recipes containing that ingredient."""
    await _create_recipes(client, auth_headers)
    r = await client.get("/api/v1/search/?ingredients=pasta")
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 1
    assert "Pasta" in results[0]["translations"][0]["title"]


async def test_search_by_multiple_ingredients_ranked(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Multi-ingredient search ranks by match count."""
    await _create_recipes(client, auth_headers)
    # carrot and zucchini appear in BOTH pasta and minestrone
    r = await client.get("/api/v1/search/?ingredients=carrot,zucchini")
    assert r.status_code == 200
    results = r.json()
    # Both pasta and minestrone should appear
    assert len(results) == 2
    # Both have 2 matches — match count should be 2 for both
    assert results[0]["ingredient_match_count"] == 2
    assert results[1]["ingredient_match_count"] == 2


async def test_search_exclude_ingredient(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Exclude filter removes recipes containing that ingredient."""
    await _create_recipes(client, auth_headers)
    r = await client.get("/api/v1/search/?exclude_ingredients=tomato")
    assert r.status_code == 200
    results = r.json()
    titles = [res["translations"][0]["title"] for res in results]
    # Beef stew has tomato — should be excluded
    assert not any("Stew" in t for t in titles)
    assert len(results) == 2


async def test_search_include_and_exclude_combined(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Include and exclude can be combined."""
    await _create_recipes(client, auth_headers)
    # Want carrot but not beef
    r = await client.get("/api/v1/search/?ingredients=carrot&exclude_ingredients=beef")
    assert r.status_code == 200
    results = r.json()
    # Pasta primavera and minestrone have carrot but no beef
    assert len(results) == 2
    titles = [res["translations"][0]["title"] for res in results]
    assert not any("Stew" in t for t in titles)


# ============================================================
# Pagination tests
# ============================================================

async def test_search_pagination(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Pagination works correctly."""
    await _create_recipes(client, auth_headers)
    r = await client.get("/api/v1/search/?per_page=2&page=1")
    assert r.status_code == 200
    assert len(r.json()) == 2

    r2 = await client.get("/api/v1/search/?per_page=2&page=2")
    assert r2.status_code == 200
    assert len(r2.json()) == 1


async def test_search_per_page_max(client: AsyncClient):
    """per_page is capped at 50."""
    r = await client.get("/api/v1/search/?per_page=100")
    assert r.status_code == 422  # Validation error
