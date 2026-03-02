# ============================================================
# tests/test_wellknown.py — tests for WebFinger and NodeInfo
# ============================================================

import pytest
from httpx import AsyncClient


# ============================================================
# WebFinger tests
# ============================================================

async def test_webfinger_by_acct(client: AsyncClient, test_user: dict):
    """WebFinger resolves acct:username@domain to an AP Actor URL."""
    response = await client.get(
        "/.well-known/webfinger",
        params={"resource": "acct:testuser@pasticcio.localhost"},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["subject"] == "acct:testuser@pasticcio.localhost"
    # Must have a self link pointing to the AP Actor
    self_link = next(l for l in data["links"] if l["rel"] == "self")
    assert self_link["type"] == "application/activity+json"
    assert "testuser" in self_link["href"]


async def test_webfinger_by_profile_url(client: AsyncClient, test_user: dict):
    """WebFinger also resolves a full profile URL."""
    response = await client.get(
        "/.well-known/webfinger",
        params={"resource": "https://pasticcio.localhost/users/testuser"},
    )
    assert response.status_code == 200
    assert response.json()["subject"] == "acct:testuser@pasticcio.localhost"


async def test_webfinger_content_type(client: AsyncClient, test_user: dict):
    """WebFinger responses must use application/jrd+json."""
    response = await client.get(
        "/.well-known/webfinger",
        params={"resource": "acct:testuser@pasticcio.localhost"},
    )
    assert "application/jrd+json" in response.headers["content-type"]


async def test_webfinger_unknown_user(client: AsyncClient):
    """WebFinger returns 404 for users that don't exist."""
    response = await client.get(
        "/.well-known/webfinger",
        params={"resource": "acct:nobody@pasticcio.localhost"},
    )
    assert response.status_code == 404


async def test_webfinger_wrong_domain(client: AsyncClient, test_user: dict):
    """WebFinger returns 404 for requests about other domains."""
    response = await client.get(
        "/.well-known/webfinger",
        params={"resource": "acct:testuser@mastodon.social"},
    )
    assert response.status_code == 404


async def test_webfinger_invalid_resource_format(client: AsyncClient):
    """WebFinger returns 400 for malformed resource parameters."""
    response = await client.get(
        "/.well-known/webfinger",
        params={"resource": "not-a-valid-resource"},
    )
    assert response.status_code == 400


async def test_webfinger_acct_missing_domain(client: AsyncClient):
    """WebFinger returns 400 for acct: without a domain part."""
    response = await client.get(
        "/.well-known/webfinger",
        params={"resource": "acct:testuser"},
    )
    assert response.status_code == 400


# ============================================================
# NodeInfo tests
# ============================================================

async def test_nodeinfo_discovery(client: AsyncClient):
    """NodeInfo discovery document points to the 2.1 endpoint."""
    response = await client.get("/.well-known/nodeinfo")
    assert response.status_code == 200

    data = response.json()
    assert "links" in data
    assert len(data["links"]) >= 1

    link = data["links"][0]
    assert "nodeinfo" in link["rel"]
    assert link["href"].endswith("/nodeinfo/2.1")


async def test_nodeinfo_21(client: AsyncClient, test_user: dict):
    """NodeInfo 2.1 returns server info with correct structure."""
    response = await client.get("/nodeinfo/2.1")
    assert response.status_code == 200

    data = response.json()
    assert data["version"] == "2.1"
    assert data["software"]["name"] == "pasticcio"
    assert "activitypub" in data["protocols"]
    assert data["usage"]["users"]["total"] >= 1
    assert isinstance(data["openRegistrations"], bool)
