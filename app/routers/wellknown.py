# ============================================================
# app/routers/wellknown.py — WebFinger and NodeInfo endpoints
# ============================================================
#
# WebFinger (RFC 7033) is the discovery mechanism used across
# the Fediverse. When a Mastodon user searches for
# @maria@pasticcio.example.org, Mastodon does:
#
#   GET https://pasticcio.example.org/.well-known/webfinger
#       ?resource=acct:maria@pasticcio.example.org
#
# We respond with a JRD (JSON Resource Descriptor) that points
# to the user's ActivityPub Actor profile URL.
#
# NodeInfo is a standard for describing a Fediverse server's
# capabilities and stats. Some clients use it to show instance
# info and choose which features to enable.

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User

router = APIRouter(tags=["discovery"])

# The MIME type required by the WebFinger spec
WEBFINGER_CONTENT_TYPE = "application/jrd+json"


# ============================================================
# WebFinger
# ============================================================

@router.get("/.well-known/webfinger")
async def webfinger(
    resource: str = Query(..., description="acct:username@domain or profile URL"),
    db: AsyncSession = Depends(get_db),
):
    """
    Resolve a Fediverse handle or profile URL to an AP Actor.

    Called by Mastodon and other clients when a user searches
    for someone by their @handle@domain address.

    The `resource` parameter is either:
      - "acct:maria@pasticcio.example.org"  (handle form)
      - "https://pasticcio.example.org/users/maria"  (URL form)
    """
    # --- Parse the resource parameter ---
    username: str | None = None

    if resource.startswith("acct:"):
        # Format: acct:username@domain
        acct = resource[len("acct:"):]
        if "@" not in acct:
            raise HTTPException(status_code=400, detail="Invalid acct: format")
        local_part, domain = acct.rsplit("@", 1)

        # Reject requests for other domains — we only know our own users
        if domain != settings.instance_domain:
            raise HTTPException(
                status_code=404,
                detail=f"This server handles {settings.instance_domain}, not {domain}",
            )
        username = local_part.lower()

    elif resource.startswith(f"https://{settings.instance_domain}/users/"):
        # Format: https://instance/users/username
        username = resource.split("/users/")[-1].lower()

    else:
        raise HTTPException(status_code=400, detail="Unsupported resource format")

    # --- Look up the user ---
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()

    if user is None or user.is_remote:
        raise HTTPException(status_code=404, detail="User not found")

    # --- Build the JRD response ---
    actor_url = f"https://{settings.instance_domain}/users/{user.username}"

    jrd = {
        "subject": f"acct:{user.username}@{settings.instance_domain}",
        "aliases": [actor_url],
        "links": [
            {
                # This rel tells the client where to find the AP Actor profile
                "rel": "self",
                "type": "application/activity+json",
                "href": actor_url,
            },
            {
                # Profile page (HTML) — optional but good practice
                "rel": "http://webfinger.net/rel/profile-page",
                "type": "text/html",
                "href": actor_url,
            },
        ],
    }

    # WebFinger responses must use application/jrd+json
    # FastAPI's JSONResponse uses application/json by default,
    # so we set the media_type explicitly.
    return JSONResponse(content=jrd, media_type=WEBFINGER_CONTENT_TYPE)


# ============================================================
# NodeInfo
# ============================================================
#
# NodeInfo is a two-step discovery:
#   1. GET /.well-known/nodeinfo  → list of NodeInfo URLs
#   2. GET /nodeinfo/2.1          → the actual server info
#
# Some Fediverse clients use this to show instance stats and
# to determine which AP features the server supports.

@router.get("/.well-known/nodeinfo")
async def nodeinfo_discovery():
    """Return the NodeInfo discovery document."""
    return JSONResponse(
        content={
            "links": [
                {
                    "rel": "http://nodeinfo.diaspora.software/ns/schema/2.1",
                    "href": f"https://{settings.instance_domain}/nodeinfo/2.1",
                }
            ]
        },
        media_type="application/json",
    )


@router.get("/nodeinfo/2.1")
async def nodeinfo(db: AsyncSession = Depends(get_db)):
    """
    Return server capabilities and stats in NodeInfo 2.1 format.

    The user count is approximate (active users in the last 30 days
    would require more tracking — we just return total for now).
    """
    # Count local (non-remote) users
    from sqlalchemy import func as sql_func
    result = await db.execute(
        select(sql_func.count(User.id)).where(User.is_remote == False)  # noqa: E712
    )
    user_count = result.scalar_one()

    return JSONResponse(
        content={
            "version": "2.1",
            "software": {
                "name": "pasticcio",
                "version": "0.1.0",
                "repository": "https://github.com/TBD/pasticcio",
            },
            "protocols": ["activitypub"],
            "usage": {
                "users": {
                    "total": user_count,
                    "activeMonth": user_count,   # approximate
                    "activeHalfyear": user_count,
                },
                "localPosts": 0,  # TODO: count published recipes
            },
            "openRegistrations": settings.enable_registrations,
            "metadata": {
                "nodeName": settings.instance_name,
                "nodeDescription": settings.instance_description,
            },
        },
        media_type="application/json",
    )
