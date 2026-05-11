# ============================================================
# tests/test_middleware.py — tests for API rate limiting middleware
# ============================================================

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


async def test_anonymous_request_allowed_within_limit(client: AsyncClient):
    """Anonymous requests within the limit are allowed."""
    with patch(
        "app.middleware.RateLimitMiddleware._check_limit",
        new=AsyncMock(return_value=True),
    ):
        response = await client.get("/health")
    assert response.status_code == 200


async def test_anonymous_request_blocked_when_limit_exceeded(
    client: AsyncClient, test_user: dict
):
    """Anonymous requests exceeding the limit return 429."""
    from app.config import settings
    settings.testing = False
    try:
        with patch(
            "app.middleware.RateLimitMiddleware._check_limit",
            new=AsyncMock(return_value=False),
        ):
            response = await client.get("/api/v1/recipes/")
        assert response.status_code == 429
        assert "detail" in response.json()
    finally:
        settings.testing = True


async def test_authenticated_request_blocked_when_limit_exceeded(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Authenticated requests exceeding the limit return 429."""
    from app.config import settings
    settings.testing = False
    try:
        with patch(
            "app.middleware.RateLimitMiddleware._check_limit",
            new=AsyncMock(return_value=False),
        ):
            response = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 429
    finally:
        settings.testing = True


async def test_health_endpoint_not_rate_limited(client: AsyncClient):
    """Health check endpoint is never rate limited."""
    # Even with a mock that would block, /health passes through
    with patch(
        "app.middleware.RateLimitMiddleware._check_limit",
        new=AsyncMock(return_value=False),
    ):
        response = await client.get("/health")
    # /health is excluded — check_limit is not called, so 200
    assert response.status_code == 200


async def test_redis_failure_allows_request(
    client: AsyncClient, test_user: dict
):
    """If Redis is unreachable, requests are allowed through (fail open)."""
    with patch(
        "app.middleware.RateLimitMiddleware._check_limit",
        new=AsyncMock(return_value=True),
    ):
        response = await client.get("/api/v1/recipes/")
    assert response.status_code == 200


async def test_check_limit_ip_exceeded():
    """_check_limit returns False when counter exceeds max."""
    from app.middleware import RateLimitMiddleware
    from fastapi import FastAPI

    app = FastAPI()
    middleware = RateLimitMiddleware(app)

    mock_redis = AsyncMock()
    mock_redis.incr = AsyncMock(return_value=9999)
    mock_redis.expire = AsyncMock()

    with patch("app.middleware.get_redis", return_value=mock_redis):
        with patch("app.middleware.settings") as mock_settings:
            mock_settings.api_ratelimit_ip_max = 60
            mock_settings.api_ratelimit_ip_window = 60
            mock_settings.api_ratelimit_user_max = 300
            mock_settings.api_ratelimit_user_window = 60

            result = await middleware._check_limit(
                "ratelimit:api:ip:1.2.3.4", 60, 60, "IP 1.2.3.4"
            )

    assert result is False


async def test_check_limit_redis_error_allows():
    """_check_limit returns True when Redis raises an exception."""
    from app.middleware import RateLimitMiddleware
    from fastapi import FastAPI

    app = FastAPI()
    middleware = RateLimitMiddleware(app)

    mock_redis = AsyncMock()
    mock_redis.incr = AsyncMock(side_effect=Exception("Redis down"))

    with patch("app.middleware.get_redis", return_value=mock_redis):
        result = await middleware._check_limit(
            "ratelimit:api:ip:1.2.3.4", 60, 60, "IP 1.2.3.4"
        )

    assert result is True
