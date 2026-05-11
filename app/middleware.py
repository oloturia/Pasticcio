# ============================================================
# app/middleware.py — API rate limiting middleware
# ============================================================
#
# This middleware applies rate limiting to all API requests.
# It uses Redis sliding window counters, keyed by:
#   - user ID for authenticated requests (JWT token)
#   - IP address for anonymous requests
#
# Excluded paths: health check, media files, AP discovery,
# API docs — these should never be rate limited.
#
# Returns 429 Too Many Requests when the limit is exceeded.

import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.ap.ratelimit import get_redis
from app.auth import decode_access_token
from app.config import settings

logger = logging.getLogger(__name__)

# Paths that are never rate limited
EXCLUDED_PREFIXES = (
    "/health",
    "/media/",
    "/.well-known/",
    "/api/docs",
    "/api/redoc",
    "/api/openapi.json",
)

RATE_LIMIT_EXCEEDED = '{"detail": "Rate limit exceeded"}'


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware for the REST API.

    Authenticated requests are limited per user ID.
    Anonymous requests are limited per IP address.
    Both limits use a sliding window counter in Redis.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        if settings.testing:
            return await call_next(request)
        path = request.url.path

        # Skip rate limiting for excluded paths
        if any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
            return await call_next(request)

        # Determine the rate limit key and limits
        user_id = self._extract_user_id(request)

        if user_id:
            key = f"ratelimit:api:user:{user_id}"
            max_requests = settings.api_ratelimit_user_max
            window_seconds = settings.api_ratelimit_user_window
            label = f"user {user_id}"
        else:
            client_ip = request.client.host if request.client else "unknown"
            key = f"ratelimit:api:ip:{client_ip}"
            max_requests = settings.api_ratelimit_ip_max
            window_seconds = settings.api_ratelimit_ip_window
            label = f"IP {client_ip}"

        # Check the rate limit
        allowed = await self._check_limit(key, max_requests, window_seconds, label)
        if not allowed:
            return Response(
                content=RATE_LIMIT_EXCEEDED,
                status_code=429,
                media_type="application/json",
            )

        return await call_next(request)

    def _extract_user_id(self, request: Request) -> str | None:
        """Extract user ID from the Authorization header if present."""
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        token = auth[len("Bearer "):]
        return decode_access_token(token)

    async def _check_limit(
        self,
        key: str,
        max_requests: int,
        window_seconds: int,
        label: str,
    ) -> bool:
        """
        Increment the counter for this key and check against the limit.
        Returns True if the request is allowed, False if it should be blocked.
        Fails open if Redis is unreachable.
        """
        try:
            redis = get_redis()
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, window_seconds)
            if count > max_requests:
                logger.warning(
                    "API rate limit exceeded for %s: %d/%d in %ds",
                    label, count, max_requests, window_seconds,
                )
                return False
        except Exception as exc:
            # Fail open — don't block requests if Redis is down
            logger.error("API rate limit Redis error for %s: %s", label, exc)
        return True
