# ============================================================
# tests/test_fork.py — tests for recipe fork endpoint
# ============================================================

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

RECIPE_PAYLOAD = {
    "translation": {
        "language": "en",
        "title": "Original Recipe",
        "description": "The original.",
        "steps": [],
    },
    "original_language": "en",
    "ingredients": [],
    "publish": True,
}

FAKE_REMOTE_ARTICLE = {
    "type": "Article",
    "id": "https://remote.example.com/users/chef/recipes/pasta",
    "name": "Remote Pasta Recipe",
    "summary": "A great pasta from a remote server.",
    "inLanguage": "en",
    "tag": [
        {"type": "Hashtag", "name": "#vegan"},
        {"type": "Hashtag", "name": "#glutenfree"},
    ],
    "pasticcio:servings": 4,
    "pasticcio:prepTime": "PT15M",
    "pasticcio:cookTime": "PT30M",
}


async def _mock_fetch(url, **kwargs):
    """Mock httpx response for remote recipe fetch."""
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = FAKE_REMOTE_ARTICLE
    return mock_resp


# ============================================================
# Fork tests
# ============================================================

async def test_fork_remote_recipe(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """An authenticated user can fork a remote recipe."""
    with patch("app.routers.recipes.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=type("R", (), {
                "status_code": 200,
                "json": lambda self: FAKE_REMOTE_ARTICLE,
            })()
        )
        response = await client.post(
            "/api/v1/recipes/fork",
            json={"ap_id": "https://remote.example.com/users/chef/recipes/pasta"},
            headers=auth_headers,
        )

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "draft"  # forks are always drafts
    assert data["forked_from"] == "https://remote.example.com/users/chef/recipes/pasta"
    assert data["author"]["username"] == "testuser"


async def test_fork_copies_title(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """The fork has the same title as the original."""
    with patch("app.routers.recipes.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=type("R", (), {
                "status_code": 200,
                "json": lambda self: FAKE_REMOTE_ARTICLE,
            })()
        )
        response = await client.post(
            "/api/v1/recipes/fork",
            json={"ap_id": "https://remote.example.com/users/chef/recipes/pasta"},
            headers=auth_headers,
        )

    data = response.json()
    assert data["translations"][0]["title"] == "Remote Pasta Recipe"


async def test_fork_copies_dietary_tags(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """The fork copies dietary tags from the original."""
    with patch("app.routers.recipes.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=type("R", (), {
                "status_code": 200,
                "json": lambda self: FAKE_REMOTE_ARTICLE,
            })()
        )
        response = await client.post(
            "/api/v1/recipes/fork",
            json={"ap_id": "https://remote.example.com/users/chef/recipes/pasta"},
            headers=auth_headers,
        )

    data = response.json()
    assert "vegan" in data["dietary_tags"]
    assert "glutenfree" in data["dietary_tags"]


async def test_fork_unauthenticated(client: AsyncClient):
    """Forking without auth returns 401."""
    response = await client.post(
        "/api/v1/recipes/fork",
        json={"ap_id": "https://remote.example.com/users/chef/recipes/pasta"},
    )
    assert response.status_code == 401


async def test_fork_non_article_returns_422(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Forking a non-Article AP object returns 422."""
    fake_note = {"type": "Note", "id": "https://remote.example.com/note/1", "content": "Hello"}
    with patch("app.routers.recipes.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=type("R", (), {
                "status_code": 200,
                "json": lambda self: fake_note,
            })()
        )
        response = await client.post(
            "/api/v1/recipes/fork",
            json={"ap_id": "https://remote.example.com/note/1"},
            headers=auth_headers,
        )
    assert response.status_code == 422


async def test_fork_unreachable_server_returns_422(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """If the remote server is unreachable, return 422."""
    import httpx
    with patch("app.routers.recipes.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.RequestError("Connection refused")
        )
        response = await client.post(
            "/api/v1/recipes/fork",
            json={"ap_id": "https://remote.example.com/users/chef/recipes/pasta"},
            headers=auth_headers,
        )
    assert response.status_code == 422
