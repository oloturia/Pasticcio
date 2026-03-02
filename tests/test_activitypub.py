# ============================================================
# tests/test_activitypub.py — tests for ActivityPub endpoints
# ============================================================
#
# Tests cover:
#   - Actor profile (GET /users/{username})
#   - Outbox (GET /users/{username}/outbox)
#   - Followers collection (GET /users/{username}/followers)
#   - Inbox — Follow handling with mocked HTTP Signature verification
#
# The Inbox tests use pytest monkeypatch to bypass HTTP Signature
# verification. This is intentional: we're testing the *business logic*
# (follower stored, Accept sent back), not the crypto layer.
# The crypto is tested separately via the signatures module.

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

# Shared recipe payload used across tests
RECIPE_PAYLOAD = {
    "translation": {
        "language": "en",
        "title": "Test Recipe",
        "description": "A test recipe.",
        "steps": [{"order": 1, "text": "Do something."}],
    },
    "original_language": "en",
    "ingredients": [],
}


# ============================================================
# Actor tests
# ============================================================

async def test_actor_returns_json_ld(client: AsyncClient, test_user: dict):
    """GET /users/{username} returns a valid ActivityPub Actor JSON-LD object."""
    response = await client.get(
        "/users/testuser",
        headers={"Accept": "application/activity+json"},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["type"] == "Person"
    assert data["preferredUsername"] == "testuser"
    assert "inbox" in data
    assert "outbox" in data
    assert "publicKey" in data


async def test_actor_has_public_key(client: AsyncClient, test_user: dict):
    """The Actor object includes a non-empty RSA public key."""
    response = await client.get("/users/testuser")
    data = response.json()

    pub_key = data["publicKey"]
    assert pub_key["id"].endswith("#main-key")
    assert pub_key["owner"] == data["id"]
    assert "BEGIN PUBLIC KEY" in pub_key["publicKeyPem"]


async def test_actor_content_type(client: AsyncClient, test_user: dict):
    """Actor endpoint responds with application/activity+json content type."""
    response = await client.get("/users/testuser")
    assert "application/activity+json" in response.headers["content-type"]


async def test_actor_inbox_outbox_urls(client: AsyncClient, test_user: dict):
    """Inbox and outbox URLs are correctly formed."""
    response = await client.get("/users/testuser")
    data = response.json()

    assert data["inbox"].endswith("/users/testuser/inbox")
    assert data["outbox"].endswith("/users/testuser/outbox")
    assert data["followers"].endswith("/users/testuser/followers")


async def test_actor_unknown_user(client: AsyncClient):
    """GET /users/{username} returns 404 for unknown users."""
    response = await client.get("/users/nobody")
    assert response.status_code == 404


async def test_registration_generates_rsa_keys(client: AsyncClient):
    """
    A newly registered user has RSA keys stored.
    We verify this indirectly: the Actor endpoint exposes the public key,
    which only exists if keys were generated at registration.
    """
    await client.post("/api/v1/auth/register", json={
        "username": "keytestuser",
        "email": "keytest@example.com",
        "password": "SecurePass123!",
    })
    response = await client.get("/users/keytestuser")
    data = response.json()
    assert "BEGIN PUBLIC KEY" in data["publicKey"]["publicKeyPem"]


# ============================================================
# Outbox tests
# ============================================================

async def test_outbox_collection_root(client: AsyncClient, test_user: dict):
    """GET /users/{username}/outbox without ?page returns OrderedCollection."""
    response = await client.get("/users/testuser/outbox")
    assert response.status_code == 200

    data = response.json()
    assert data["type"] == "OrderedCollection"
    assert "totalItems" in data
    assert "first" in data


async def test_outbox_starts_empty(client: AsyncClient, test_user: dict):
    """A new user's outbox has totalItems=0."""
    response = await client.get("/users/testuser/outbox")
    assert response.json()["totalItems"] == 0


async def test_outbox_page_empty(client: AsyncClient, test_user: dict):
    """Outbox page 1 for a user with no recipes has an empty orderedItems."""
    response = await client.get("/users/testuser/outbox?page=1")
    assert response.status_code == 200

    data = response.json()
    assert data["type"] == "OrderedCollectionPage"
    assert data["orderedItems"] == []


async def test_outbox_contains_published_recipes(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Published recipes appear in the outbox as Create{Article} activities."""
    # Create and publish a recipe
    await client.post(
        "/api/v1/recipes/",
        json={**RECIPE_PAYLOAD, "publish": True},
        headers=auth_headers,
    )

    response = await client.get("/users/testuser/outbox?page=1")
    data = response.json()

    assert data["totalItems"] == 1
    assert len(data["orderedItems"]) == 1

    activity = data["orderedItems"][0]
    assert activity["type"] == "Create"
    assert activity["object"]["type"] == "Article"
    assert activity["object"]["name"] == "Test Recipe"


async def test_outbox_draft_recipes_not_included(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Draft recipes do NOT appear in the outbox."""
    # Create a draft (no publish=True)
    await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)

    response = await client.get("/users/testuser/outbox?page=1")
    data = response.json()

    assert data["totalItems"] == 0
    assert data["orderedItems"] == []


async def test_outbox_article_has_hashtags(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """The Article in the outbox has tag objects for dietary tags."""
    payload = {**RECIPE_PAYLOAD, "publish": True, "dietary_tags": ["vegan"]}
    await client.post("/api/v1/recipes/", json=payload, headers=auth_headers)

    response = await client.get("/users/testuser/outbox?page=1")
    article = response.json()["orderedItems"][0]["object"]

    tag_names = [t["name"] for t in article["tag"]]
    assert "#vegan" in tag_names


async def test_outbox_unknown_user(client: AsyncClient):
    """Outbox for unknown user returns 404."""
    response = await client.get("/users/nobody/outbox")
    assert response.status_code == 404


# ============================================================
# Followers collection tests
# ============================================================

async def test_followers_collection(client: AsyncClient, test_user: dict):
    """GET /users/{username}/followers returns an OrderedCollection."""
    response = await client.get("/users/testuser/followers")
    assert response.status_code == 200

    data = response.json()
    assert data["type"] == "OrderedCollection"
    assert data["totalItems"] == 0


async def test_followers_unknown_user(client: AsyncClient):
    """Followers collection for unknown user returns 404."""
    response = await client.get("/users/nobody/followers")
    assert response.status_code == 404


# ============================================================
# Inbox tests (with mocked signature verification)
# ============================================================
#
# We mock two things:
#   1. verify_request() → always returns True (skip crypto)
#   2. _fetch_remote_actor() → returns a fake actor with a public key
#   3. _deliver_activity() → no-op (don't actually POST to remote servers)
#
# This lets us test the business logic of the inbox handler
# without needing a real remote server or valid HTTP Signatures.

FAKE_REMOTE_ACTOR = {
    "type": "Person",
    "id": "https://mastodon.social/users/remoteuser",
    "inbox": "https://mastodon.social/users/remoteuser/inbox",
    "publicKey": {
        "id": "https://mastodon.social/users/remoteuser#main-key",
        "owner": "https://mastodon.social/users/remoteuser",
        "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----",
    },
}

FOLLOW_ACTIVITY = {
    "type": "Follow",
    "id": "https://mastodon.social/users/remoteuser#follow-1",
    "actor": "https://mastodon.social/users/remoteuser",
    "object": "https://pasticcio.localhost/users/testuser",
}


async def test_inbox_follow_stores_follower(
    client: AsyncClient, test_user: dict
):
    """A Follow activity adds the remote actor to the followers list."""
    with (
        patch("app.routers.activitypub.verify_request", return_value=True),
        patch(
            "app.routers.activitypub._fetch_remote_actor",
            new=AsyncMock(return_value=FAKE_REMOTE_ACTOR),
        ),
        patch(
            "app.routers.activitypub._deliver_activity",
            new=AsyncMock(),
        ),
    ):
        response = await client.post(
            "/users/testuser/inbox",
            content=json.dumps(FOLLOW_ACTIVITY),
            headers={"Content-Type": "application/activity+json"},
        )
        assert response.status_code == 202

    # The follower count should now be 1
    followers = await client.get("/users/testuser/followers")
    assert followers.json()["totalItems"] == 1


async def test_inbox_follow_idempotent(client: AsyncClient, test_user: dict):
    """Sending a Follow twice does not create duplicate followers."""
    with (
        patch("app.routers.activitypub.verify_request", return_value=True),
        patch(
            "app.routers.activitypub._fetch_remote_actor",
            new=AsyncMock(return_value=FAKE_REMOTE_ACTOR),
        ),
        patch("app.routers.activitypub._deliver_activity", new=AsyncMock()),
    ):
        await client.post(
            "/users/testuser/inbox",
            content=json.dumps(FOLLOW_ACTIVITY),
            headers={"Content-Type": "application/activity+json"},
        )
        await client.post(
            "/users/testuser/inbox",
            content=json.dumps(FOLLOW_ACTIVITY),
            headers={"Content-Type": "application/activity+json"},
        )

    followers = await client.get("/users/testuser/followers")
    assert followers.json()["totalItems"] == 1


async def test_inbox_unfollow_removes_follower(client: AsyncClient, test_user: dict):
    """An Undo{Follow} removes the remote actor from followers."""
    # First, follow
    with (
        patch("app.routers.activitypub.verify_request", return_value=True),
        patch(
            "app.routers.activitypub._fetch_remote_actor",
            new=AsyncMock(return_value=FAKE_REMOTE_ACTOR),
        ),
        patch("app.routers.activitypub._deliver_activity", new=AsyncMock()),
    ):
        await client.post(
            "/users/testuser/inbox",
            content=json.dumps(FOLLOW_ACTIVITY),
            headers={"Content-Type": "application/activity+json"},
        )

        # Then, unfollow
        undo_activity = {
            "type": "Undo",
            "actor": "https://mastodon.social/users/remoteuser",
            "object": FOLLOW_ACTIVITY,
        }
        response = await client.post(
            "/users/testuser/inbox",
            content=json.dumps(undo_activity),
            headers={"Content-Type": "application/activity+json"},
        )
        assert response.status_code == 202

    followers = await client.get("/users/testuser/followers")
    assert followers.json()["totalItems"] == 0


async def test_inbox_invalid_signature_returns_401(client: AsyncClient, test_user: dict):
    """
    A request with an invalid HTTP Signature is rejected with 401.
    Here we do NOT mock verify_request, so it runs the real check
    and fails (because the test request has no valid signature).
    """
    with patch(
        "app.routers.activitypub._fetch_remote_actor",
        new=AsyncMock(return_value=FAKE_REMOTE_ACTOR),
    ):
        response = await client.post(
            "/users/testuser/inbox",
            content=json.dumps(FOLLOW_ACTIVITY),
            headers={"Content-Type": "application/activity+json"},
        )
    assert response.status_code == 401


async def test_inbox_missing_actor_returns_400(client: AsyncClient, test_user: dict):
    """An activity without an actor field returns 400."""
    with (
        patch("app.routers.activitypub.verify_request", return_value=True),
        patch(
            "app.routers.activitypub._fetch_remote_actor",
            new=AsyncMock(return_value=FAKE_REMOTE_ACTOR),
        ),
    ):
        response = await client.post(
            "/users/testuser/inbox",
            content=json.dumps({"type": "Follow"}),  # no actor
            headers={"Content-Type": "application/activity+json"},
        )
    assert response.status_code == 400


async def test_inbox_unknown_user_returns_404(client: AsyncClient):
    """Inbox for an unknown local user returns 404."""
    response = await client.post(
        "/users/nobody/inbox",
        content=json.dumps(FOLLOW_ACTIVITY),
        headers={"Content-Type": "application/activity+json"},
    )
    assert response.status_code == 404
