# ============================================================
# tests/test_ratelimit.py — tests for AP inbox rate limiting
# ============================================================

import json
from unittest.mock import AsyncMock, MagicMock, patch

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

FOLLOW_ACTIVITY = {
    "type": "Follow",
    "id": "https://remote.example.com/users/remoteuser#follow-1",
    "actor": "https://remote.example.com/users/remoteuser",
    "object": "https://pasticcio.localhost/users/testuser",
}


async def _post_to_inbox(client, activity, headers=None):
    """Post an activity to the inbox with mocked signature and AP actor fetch."""
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
        return await client.post(
            "/users/testuser/inbox",
            content=json.dumps(activity),
            headers={"Content-Type": "application/activity+json"},
        )


async def test_inbox_allowed_within_limit(
    client: AsyncClient, test_user: dict
):
    """Requests within the rate limit are accepted normally."""
    with patch(
        "app.routers.activitypub.check_rate_limit",
        new=AsyncMock(return_value=(True, "")),
    ):
        response = await _post_to_inbox(client, FOLLOW_ACTIVITY)
    assert response.status_code == 202


async def test_inbox_blocked_when_limit_exceeded(
    client: AsyncClient, test_user: dict
):
    """Requests exceeding the rate limit return 429."""
    with patch(
        "app.routers.activitypub.check_rate_limit",
        new=AsyncMock(return_value=(False, "Rate limit exceeded for IP 127.0.0.1")),
    ):
        response = await _post_to_inbox(client, FOLLOW_ACTIVITY)
    assert response.status_code == 429


async def test_inbox_rate_limit_includes_reason(
    client: AsyncClient, test_user: dict
):
    """429 response includes a detail message."""
    with patch(
        "app.routers.activitypub.check_rate_limit",
        new=AsyncMock(return_value=(False, "Rate limit exceeded for domain remote.example.com")),
    ):
        response = await _post_to_inbox(client, FOLLOW_ACTIVITY)
    assert response.status_code == 429
    assert "detail" in response.json()


async def test_rate_limit_redis_failure_allows_request(
    client: AsyncClient, test_user: dict
):
    """If Redis is unreachable, the request is allowed through (fail open)."""
    # check_rate_limit swallows Redis errors and returns True
    # We simulate this by having it return allowed=True despite an error
    with patch(
        "app.routers.activitypub.check_rate_limit",
        new=AsyncMock(return_value=(True, "")),
    ):
        response = await _post_to_inbox(client, FOLLOW_ACTIVITY)
    assert response.status_code == 202


async def test_check_rate_limit_ip_exceeded():
    """check_rate_limit returns False when IP counter exceeds max."""
    from app.ap.ratelimit import check_rate_limit

    mock_redis = AsyncMock()
    # First call (IP check) returns count > max, second call (domain) not reached
    mock_redis.incr = AsyncMock(return_value=9999)
    mock_redis.expire = AsyncMock()

    with patch("app.ap.ratelimit.get_redis", return_value=mock_redis):
        with patch("app.ap.ratelimit.settings") as mock_settings:
            mock_settings.inbox_ratelimit_ip_max = 300
            mock_settings.inbox_ratelimit_ip_window = 300
            mock_settings.inbox_ratelimit_domain_max = 600
            mock_settings.inbox_ratelimit_domain_window = 300
            mock_settings.redis_url = "redis://redis:6379/0"

            allowed, reason = await check_rate_limit(
                "1.2.3.4", "https://remote.example.com/users/foo"
            )

    assert allowed is False
    assert "1.2.3.4" in reason


async def test_check_rate_limit_domain_exceeded():
    """check_rate_limit returns False when domain counter exceeds max."""
    from app.ap.ratelimit import check_rate_limit

    call_count = 0

    async def mock_incr(key):
        nonlocal call_count
        call_count += 1
        # IP check passes (count=1), domain check fails (count=9999)
        return 1 if call_count == 1 else 9999

    mock_redis = AsyncMock()
    mock_redis.incr = mock_incr
    mock_redis.expire = AsyncMock()

    with patch("app.ap.ratelimit.get_redis", return_value=mock_redis):
        with patch("app.ap.ratelimit.settings") as mock_settings:
            mock_settings.inbox_ratelimit_ip_max = 300
            mock_settings.inbox_ratelimit_ip_window = 300
            mock_settings.inbox_ratelimit_domain_max = 600
            mock_settings.inbox_ratelimit_domain_window = 300

            allowed, reason = await check_rate_limit(
                "1.2.3.4", "https://remote.example.com/users/foo"
            )

    assert allowed is False
    assert "remote.example.com" in reason


async def test_check_rate_limit_redis_error_allows():
    """If Redis raises an exception, check_rate_limit fails open."""
    from app.ap.ratelimit import check_rate_limit

    mock_redis = AsyncMock()
    mock_redis.incr = AsyncMock(side_effect=Exception("Redis connection refused"))

    with patch("app.ap.ratelimit.get_redis", return_value=mock_redis):
        with patch("app.ap.ratelimit.settings") as mock_settings:
            mock_settings.inbox_ratelimit_ip_max = 300
            mock_settings.inbox_ratelimit_ip_window = 300
            mock_settings.inbox_ratelimit_domain_max = 600
            mock_settings.inbox_ratelimit_domain_window = 300

            allowed, reason = await check_rate_limit(
                "1.2.3.4", "https://remote.example.com/users/foo"
            )

    assert allowed is True
