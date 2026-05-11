# ============================================================
# app/ap/instances.py — known instance tracking
# ============================================================
#
# This module handles:
#   1. Recording domains when we receive AP activities
#   2. Checking NodeInfo to identify Pasticcio instances
#   3. Querying known Pasticcio instances for federated search
#
# NodeInfo (https://nodeinfo.diaspora.software/) is a standard
# protocol that AP servers use to expose metadata about themselves,
# including which software they run. We use it to distinguish
# Pasticcio instances from Mastodon, Pleroma, etc.

import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.known_instance import KnownInstance

logger = logging.getLogger(__name__)

TIMEOUT = 5.0


def _domain_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return url


async def record_instance(actor_url: str, db: AsyncSession) -> None:
    """
    Record or update a known instance based on an incoming AP activity.
    Called from the inbox whenever we receive an activity from a remote actor.
    Silently ignores errors to avoid blocking inbox processing.
    """
    try:
        domain = _domain_from_url(actor_url)
        if not domain:
            return

        result = await db.execute(
            select(KnownInstance).where(KnownInstance.domain == domain)
        )
        instance = result.scalar_one_or_none()

        now = datetime.now(timezone.utc)

        if instance:
            instance.last_seen = now
        else:
            instance = KnownInstance(
                domain=domain,
                last_seen=now,
            )
            db.add(instance)
            # Enqueue NodeInfo check as a background task
            try:
                from app.tasks.instances import check_nodeinfo
                check_nodeinfo.delay(domain)
            except Exception as exc:
                logger.debug("Could not enqueue NodeInfo check for %s: %s", domain, exc)

        await db.flush()
    except Exception as exc:
        logger.warning("record_instance failed for %s: %s", actor_url, exc)


async def get_pasticcio_instances(db: AsyncSession) -> list[str]:
    """Return a list of known Pasticcio instance domains."""
    result = await db.execute(
        select(KnownInstance.domain).where(KnownInstance.is_pasticcio.is_(True))
    )
    return [row[0] for row in result.fetchall()]


async def fetch_nodeinfo(domain: str) -> dict | None:
    """
    Fetch NodeInfo for a domain.
    Returns the NodeInfo dict or None if unavailable.
    """
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            # Step 1: fetch /.well-known/nodeinfo to get the actual URL
            discovery = await client.get(
                f"https://{domain}/.well-known/nodeinfo",
                follow_redirects=True,
            )
            if discovery.status_code != 200:
                return None

            links = discovery.json().get("links", [])
            nodeinfo_url = None
            for link in links:
                # Prefer 2.1, fall back to 2.0
                if "2.1" in link.get("rel", "") or "2.0" in link.get("rel", ""):
                    nodeinfo_url = link.get("href")
                    break

            if not nodeinfo_url:
                return None

            # Step 2: fetch the actual NodeInfo document
            nodeinfo = await client.get(nodeinfo_url, follow_redirects=True)
            if nodeinfo.status_code == 200:
                return nodeinfo.json()
    except httpx.RequestError as exc:
        logger.debug("NodeInfo fetch failed for %s: %s", domain, exc)
    return None
