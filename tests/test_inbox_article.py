# ============================================================
# tests/test_inbox_article.py — tests for Update/Delete Article
# ============================================================

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

FAKE_REMOTE_ACTOR = {
    "type": "Person",
    "id": "https://remote.example.com/users/remoteuser",
    "inbox": "https://remote.example.com/users/remoteuser/inbox",
    "publicKey": {
        "id": "https://remote.example.com/users/remoteuser#main-key",
        "owner": "https://remote.example.com/users/remoteuser",
        "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----",
    },
}

REMOTE_ACTOR_URL = "https://remote.example.com/users/remoteuser"

RECIPE_PAYLOAD = {
    "translation": {
        "language": "en",
        "title": "Article Test Recipe",
        "description": "For AP article tests.",
        "steps": [],
    },
    "original_language": "en",
    "ingredients": [],
    "publish": True,
}


async def _post_to_inbox(client, username, activity, actor_url=REMOTE_ACTOR_URL):
    fake_actor = {**FAKE_REMOTE_ACTOR, "id": actor_url}
    with (
        patch("app.routers.activitypub.verify_request", return_value=True),
        patch(
            "app.routers.activitypub._fetch_remote_actor",
            new=AsyncMock(return_value=fake_actor),
        ),
    ):
        return await client.post(
            f"/users/{username}/inbox",
            content=json.dumps(activity),
            headers={"Content-Type": "application/activity+json"},
        )


async def _create_published_recipe(client, auth_headers):
    r = await client.post(
        "/api/v1/recipes/",
        json={**RECIPE_PAYLOAD, "publish": True},
        headers=auth_headers,
    )
    assert r.status_code == 201
    return r.json()


# ============================================================
# Update{Article} tests
# ============================================================

async def test_inbox_update_article_acknowledged(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Update{Article} is accepted with 202."""
    update_activity = {
        "type": "Update",
        "actor": REMOTE_ACTOR_URL,
        "object": {
            "type": "Article",
            "id": "https://remote.example.com/users/remoteuser/recipes/pasta",
            "name": "Updated Pasta",
        },
    }
    response = await _post_to_inbox(client, "testuser", update_activity)
    assert response.status_code == 202


async def test_inbox_update_article_does_not_modify_local_recipe(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Update{Article} does not modify a local recipe even if AP IDs match."""
    recipe = await _create_published_recipe(client, auth_headers)
    original_title = recipe["translations"][0]["title"]

    update_activity = {
        "type": "Update",
        "actor": REMOTE_ACTOR_URL,
        "object": {
            "type": "Article",
            "id": recipe["ap_id"],
            "name": "Hacked title",
        },
    }
    await _post_to_inbox(client, "testuser", update_activity)

    # Recipe should be unchanged
    r = await client.get(f"/api/v1/recipes/{recipe['id']}")
    assert r.json()["translations"][0]["title"] == original_title


async def test_inbox_update_non_article_ignored(
    client: AsyncClient, test_user: dict
):
    """Update of a non-Article type is silently ignored."""
    update_activity = {
        "type": "Update",
        "actor": REMOTE_ACTOR_URL,
        "object": {
            "type": "Person",
            "id": REMOTE_ACTOR_URL,
            "name": "Updated Name",
        },
    }
    response = await _post_to_inbox(client, "testuser", update_activity)
    assert response.status_code == 202


# ============================================================
# Delete{Article} tests
# ============================================================

async def test_inbox_delete_article_removes_local_recipe(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Delete{Article} soft-deletes a local recipe if actor is the author."""
    # The local user's AP ID is the actor
    local_actor_url = f"https://pasticcio.localhost/users/testuser"
    recipe = await _create_published_recipe(client, auth_headers)

    delete_activity = {
        "type": "Delete",
        "actor": local_actor_url,
        "object": recipe["ap_id"],
    }

    fake_local_actor = {
        **FAKE_REMOTE_ACTOR,
        "id": local_actor_url,
    }
    with (
        patch("app.routers.activitypub.verify_request", return_value=True),
        patch(
            "app.routers.activitypub._fetch_remote_actor",
            new=AsyncMock(return_value=fake_local_actor),
        ),
    ):
        response = await client.post(
            "/users/testuser/inbox",
            content=json.dumps(delete_activity),
            headers={"Content-Type": "application/activity+json"},
        )

    assert response.status_code == 202

    # Recipe should now return 404
    r = await client.get(f"/api/v1/recipes/{recipe['id']}")
    assert r.status_code == 404


async def test_inbox_delete_article_wrong_actor_ignored(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Delete{Article} from a different actor is ignored."""
    recipe = await _create_published_recipe(client, auth_headers)

    delete_activity = {
        "type": "Delete",
        "actor": REMOTE_ACTOR_URL,  # not the author
        "object": recipe["ap_id"],
    }
    await _post_to_inbox(client, "testuser", delete_activity)

    # Recipe should still exist
    r = await client.get(f"/api/v1/recipes/{recipe['id']}")
    assert r.status_code == 200


async def test_inbox_delete_article_nonexistent_ignored(
    client: AsyncClient, test_user: dict
):
    """Delete{Article} for a recipe we don't have is silently ignored."""
    delete_activity = {
        "type": "Delete",
        "actor": REMOTE_ACTOR_URL,
        "object": "https://remote.example.com/users/remoteuser/recipes/nonexistent",
    }
    response = await _post_to_inbox(client, "testuser", delete_activity)
    assert response.status_code == 202


async def test_inbox_delete_article_dict_object(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Delete{Article} works when object is a dict (not Mastodon style)."""
    local_actor_url = "https://pasticcio.localhost/users/testuser"
    recipe = await _create_published_recipe(client, auth_headers)

    delete_activity = {
        "type": "Delete",
        "actor": local_actor_url,
        "object": {
            "type": "Article",
            "id": recipe["ap_id"],
        },
    }

    fake_local_actor = {**FAKE_REMOTE_ACTOR, "id": local_actor_url}
    with (
        patch("app.routers.activitypub.verify_request", return_value=True),
        patch(
            "app.routers.activitypub._fetch_remote_actor",
            new=AsyncMock(return_value=fake_local_actor),
        ),
    ):
        response = await client.post(
            "/users/testuser/inbox",
            content=json.dumps(delete_activity),
            headers={"Content-Type": "application/activity+json"},
        )

    assert response.status_code == 202
    r = await client.get(f"/api/v1/recipes/{recipe['id']}")
    assert r.status_code == 404
