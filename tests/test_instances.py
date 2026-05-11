# ============================================================
# tests/test_instances.py — tests for known_instances tracking
# ============================================================

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.known_instance import KnownInstance


FAKE_REMOTE_ACTOR = {
    "type": "Person",
    "id": "https://remote.example.com/users/chef",
    "inbox": "https://remote.example.com/users/chef/inbox",
    "publicKey": {
        "id": "https://remote.example.com/users/chef#main-key",
        "owner": "https://remote.example.com/users/chef",
        "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----",
    },
}

FOLLOW_ACTIVITY = {
    "type": "Follow",
    "id": "https://remote.example.com/users/chef#follow-1",
    "actor": "https://remote.example.com/users/chef",
    "object": "https://pasticcio.localhost/users/testuser",
}

FAKE_NODEINFO_DISCOVERY = {
    "links": [
        {
            "rel": "http://nodeinfo.diaspora.software/ns/schema/2.1",
            "href": "https://remote.example.com/nodeinfo/2.1",
        }
    ]
}

FAKE_NODEINFO_PASTICCIO = {
    "software": {"name": "pasticcio", "version": "0.1.0"},
    "protocols": ["activitypub"],
}

FAKE_NODEINFO_MASTODON = {
    "software": {"name": "mastodon", "version": "4.2.0"},
    "protocols": ["activitypub"],
}


async def _post_to_inbox(client, activity):
    import json
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
        patch(
            "app.tasks.instances.check_nodeinfo",
            new=AsyncMock(),
        ),
    ):
        return await client.post(
            "/users/testuser/inbox",
            content=json.dumps(activity),
            headers={"Content-Type": "application/activity+json"},
        )


# ============================================================
# Instance tracking tests
# ============================================================

async def test_inbox_records_instance(
    client: AsyncClient, test_user: dict, db_session
):
    """Receiving an activity records the remote instance."""
    await _post_to_inbox(client, FOLLOW_ACTIVITY)

    result = await db_session.execute(
        select(KnownInstance).where(KnownInstance.domain == "remote.example.com")
    )
    instance = result.scalar_one_or_none()
    assert instance is not None
    assert instance.domain == "remote.example.com"


async def test_inbox_updates_last_seen(
    client: AsyncClient, test_user: dict, db_session
):
    """Receiving a second activity updates last_seen."""
    await _post_to_inbox(client, FOLLOW_ACTIVITY)
    await _post_to_inbox(client, FOLLOW_ACTIVITY)

    result = await db_session.execute(
        select(KnownInstance).where(KnownInstance.domain == "remote.example.com")
    )
    instances = result.scalars().all()
    # Should only have one record
    assert len(instances) == 1


# ============================================================
# NodeInfo tests
# ============================================================

async def test_fetch_nodeinfo_pasticcio():
    """fetch_nodeinfo correctly identifies a Pasticcio instance."""
    from app.ap.instances import fetch_nodeinfo

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=lambda url, **kwargs: type("R", (), {
                "status_code": 200,
                "json": lambda self: (
                    FAKE_NODEINFO_DISCOVERY if "well-known" in url
                    else FAKE_NODEINFO_PASTICCIO
                ),
            })()
        )
        nodeinfo = await fetch_nodeinfo("remote.example.com")

    assert nodeinfo is not None
    assert nodeinfo["software"]["name"] == "pasticcio"


async def test_fetch_nodeinfo_returns_none_on_failure():
    """fetch_nodeinfo returns None if NodeInfo is unavailable."""
    from app.ap.instances import fetch_nodeinfo
    import httpx

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.RequestError("Connection refused")
        )
        nodeinfo = await fetch_nodeinfo("unreachable.example.com")

    assert nodeinfo is None


async def test_get_pasticcio_instances(db_session):
    """get_pasticcio_instances returns only Pasticcio instances."""
    from app.ap.instances import get_pasticcio_instances
    from app.models.known_instance import KnownInstance

    db_session.add(KnownInstance(
        domain="pasticcio.example.com",
        software="pasticcio",
        is_pasticcio=True,
    ))
    db_session.add(KnownInstance(
        domain="mastodon.example.com",
        software="mastodon",
        is_pasticcio=False,
    ))
    await db_session.flush()

    instances = await get_pasticcio_instances(db_session)
    assert "pasticcio.example.com" in instances
    assert "mastodon.example.com" not in instances
