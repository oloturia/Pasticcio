# ============================================================
# tests/test_lookup.py — tests for remote lookup endpoint
# ============================================================

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

FAKE_ACTOR = {
    "type": "Person",
    "id": "https://remote.example.com/users/chef",
    "preferredUsername": "chef",
    "name": "Chef Remote",
    "summary": "<p>A great cook</p>",
    "url": "https://remote.example.com/@chef",
    "icon": {"type": "Image", "url": "https://remote.example.com/avatar.jpg"},
    "outbox": "https://remote.example.com/users/chef/outbox",
}

FAKE_OUTBOX = {
    "type": "OrderedCollection",
    "totalItems": 2,
    "first": "https://remote.example.com/users/chef/outbox?page=1",
}

FAKE_OUTBOX_PAGE = {
    "type": "OrderedCollectionPage",
    "orderedItems": [
        {
            "type": "Create",
            "object": {
                "type": "Article",
                "id": "https://remote.example.com/users/chef/recipes/pasta",
                "name": "Remote Pasta",
                "summary": "A great pasta",
                "inLanguage": "en",
                "tag": [{"type": "Hashtag", "name": "#vegan"}],
            },
        },
        {
            "type": "Create",
            "object": {
                "type": "Article",
                "id": "https://remote.example.com/users/chef/recipes/soup",
                "name": "Remote Soup",
                "inLanguage": "it",
                "tag": [],
            },
        },
    ],
}

FAKE_ARTICLE = {
    "type": "Article",
    "id": "https://remote.example.com/users/chef/recipes/pasta",
    "name": "Remote Pasta",
    "summary": "A great pasta",
    "inLanguage": "en",
    "attributedTo": "https://remote.example.com/users/chef",
    "tag": [{"type": "Hashtag", "name": "#vegan"}],
    "pasticcio:servings": 4,
    "pasticcio:prepTime": "PT15M",
}

FAKE_WEBFINGER = {
    "links": [
        {
            "rel": "self",
            "type": "application/activity+json",
            "href": "https://remote.example.com/users/chef",
        }
    ]
}


def _make_mock_fetch(responses: dict):
    """Create a mock _fetch_json that returns different responses per URL."""
    async def mock_fetch(url: str) -> dict | None:
        return responses.get(url)
    return mock_fetch


# ============================================================
# User lookup tests
# ============================================================

async def test_lookup_user_by_handle(client: AsyncClient):
    """Lookup by handle returns user profile and recipes."""
    with (
        patch("app.routers.lookup._webfinger", new=AsyncMock(
            return_value="https://remote.example.com/users/chef"
        )),
        patch("app.routers.lookup._fetch_json", new=_make_mock_fetch({
            "https://remote.example.com/users/chef": FAKE_ACTOR,
            "https://remote.example.com/users/chef/outbox": FAKE_OUTBOX,
            "https://remote.example.com/users/chef/outbox?page=1": FAKE_OUTBOX_PAGE,
        })),
    ):
        response = await client.get("/api/v1/lookup/?handle=@chef@remote.example.com")

    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "chef"
    assert data["display_name"] == "Chef Remote"
    assert data["bio"] == "A great cook"
    assert data["avatar_url"] == "https://remote.example.com/avatar.jpg"
    assert data["instance_domain"] == "remote.example.com"
    assert data["total_recipes"] == 2
    assert len(data["recipes"]) == 2
    assert data["recipes"][0]["title"] == "Remote Pasta"
    assert "vegan" in data["recipes"][0]["dietary_tags"]


async def test_lookup_user_not_found(client: AsyncClient):
    """Lookup of nonexistent user returns 404."""
    with patch("app.routers.lookup._webfinger", new=AsyncMock(return_value=None)):
        response = await client.get("/api/v1/lookup/?handle=@nobody@remote.example.com")
    assert response.status_code == 404


async def test_lookup_user_invalid_handle(client: AsyncClient):
    """Handle without domain returns 400."""
    response = await client.get("/api/v1/lookup/?handle=@chef")
    assert response.status_code == 400


async def test_lookup_user_strips_leading_at(client: AsyncClient):
    """Leading @ is optional — both @chef@domain and chef@domain work."""
    with (
        patch("app.routers.lookup._webfinger", new=AsyncMock(
            return_value="https://remote.example.com/users/chef"
        )),
        patch("app.routers.lookup._fetch_json", new=_make_mock_fetch({
            "https://remote.example.com/users/chef": FAKE_ACTOR,
            "https://remote.example.com/users/chef/outbox": FAKE_OUTBOX,
            "https://remote.example.com/users/chef/outbox?page=1": FAKE_OUTBOX_PAGE,
        })),
    ):
        r1 = await client.get("/api/v1/lookup/?handle=@chef@remote.example.com")
        r2 = await client.get("/api/v1/lookup/?handle=chef@remote.example.com")

    assert r1.status_code == 200
    assert r2.status_code == 200


# ============================================================
# Recipe URL lookup tests
# ============================================================

async def test_lookup_recipe_by_url(client: AsyncClient):
    """Lookup by URL returns recipe preview."""
    with patch("app.routers.lookup._fetch_json", new=_make_mock_fetch({
        "https://remote.example.com/users/chef/recipes/pasta": FAKE_ARTICLE,
        "https://remote.example.com/users/chef": FAKE_ACTOR,
    })):
        response = await client.get(
            "/api/v1/lookup/?url=https://remote.example.com/users/chef/recipes/pasta"
        )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Remote Pasta"
    assert data["ap_id"] == "https://remote.example.com/users/chef/recipes/pasta"
    assert "vegan" in data["dietary_tags"]
    assert data["author_name"] == "Chef Remote"
    assert data["instance_domain"] == "remote.example.com"
    assert data["servings"] == 4


async def test_lookup_recipe_not_found(client: AsyncClient):
    """Lookup of nonexistent recipe URL returns 404."""
    with patch("app.routers.lookup._fetch_json", new=AsyncMock(return_value=None)):
        response = await client.get(
            "/api/v1/lookup/?url=https://remote.example.com/recipes/nonexistent"
        )
    assert response.status_code == 404


async def test_lookup_recipe_non_article_returns_422(client: AsyncClient):
    """Lookup of a non-Article AP object returns 422."""
    fake_note = {"type": "Note", "id": "https://remote.example.com/note/1"}
    with patch("app.routers.lookup._fetch_json", new=_make_mock_fetch({
        "https://remote.example.com/note/1": fake_note,
    })):
        response = await client.get(
            "/api/v1/lookup/?url=https://remote.example.com/note/1"
        )
    assert response.status_code == 422


# ============================================================
# Error handling tests
# ============================================================

async def test_lookup_requires_handle_or_url(client: AsyncClient):
    """Lookup without parameters returns 400."""
    response = await client.get("/api/v1/lookup/")
    assert response.status_code == 400


async def test_lookup_rejects_both_handle_and_url(client: AsyncClient):
    """Providing both handle and url returns 400."""
    response = await client.get(
        "/api/v1/lookup/?handle=@chef@remote.example.com&url=https://remote.example.com/recipe/1"
    )
    assert response.status_code == 400
