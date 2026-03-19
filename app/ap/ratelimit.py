# ============================================================
# app/ap/ratelimit.py — rate limiting for the AP inbox
# ============================================================
#
# Implements a sliding window counter using Redis INCR + EXPIRE.
# Two limits are checked for every inbox request:
#   1. Per client IP address
#   2. Per remote server domain (extracted from the actor URL)
#
# How sliding window works here:
#   - On each request we INCR a Redis key
#   - If the key is new (TTL == -1) we set its expiry to the window size
#   - If the counter exceeds the max we return 429
#
# This is not a perfect sliding window (it resets hard at window end)
# but it is simple, fast, and good enough for federation rate limiting.
# A true sliding window would require a sorted set per client.

from __future__ import annotations

import logging
from urllib.parse import urlparse

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

# Redis client — created once at module load, reused across requests.
# decode_responses=True so we get strings back instead of bytes.
_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Return the shared async Redis client, creating it if needed."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            encoding="utf-8",
        )
    return _redis


def _domain_from_url(url: str) -> str:
    """
    Extract the domain from a URL.
    e.g. "https://mastodon.social/users/foo" → "mastodon.social"
    Returns the full URL if parsing fails (safe fallback).
    """
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url


async def check_rate_limit(
    client_ip: str,
    actor_url: str,
) -> tuple[bool, str]:
    """
    Check both IP and domain rate limits for an inbox request.

    Returns:
        (allowed, reason)
        allowed=True  → request is within limits, proceed
        allowed=False → limit exceeded, return 429
        reason        → human-readable description of which limit was hit
    """
    redis = get_redis()
    domain = _domain_from_url(actor_url)

    checks = [
        (
            f"ratelimit:ip:{client_ip}",
            settings.inbox_ratelimit_ip_max,
            settings.inbox_ratelimit_ip_window,
            f"IP {client_ip}",
        ),
        (
            f"ratelimit:domain:{domain}",
            settings.inbox_ratelimit_domain_max,
            settings.inbox_ratelimit_domain_window,
            f"domain {domain}",
        ),
    ]

    for key, max_requests, window_seconds, label in checks:
        try:
            count = await redis.incr(key)
            if count == 1:
                # Key was just created — set its expiry
                await redis.expire(key, window_seconds)
            if count > max_requests:
                logger.warning(
                    "Rate limit exceeded for %s: %d/%d in %ds window",
                    label, count, max_requests, window_seconds,
                )
                return False, f"Rate limit exceeded for {label}"
        except Exception as exc:
            # If Redis is unreachable, fail open — better to let the
            # request through than to block all federation traffic
            logger.error("Rate limit Redis error for %s: %s", label, exc)

    return True, ""
