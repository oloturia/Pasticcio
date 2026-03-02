# ============================================================
# app/routers/activitypub.py — ActivityPub endpoints
# ============================================================
#
# Endpoints:
#   GET  /users/{username}          → Actor profile (JSON-LD)
#   GET  /users/{username}/outbox   → Published activities
#   POST /users/{username}/inbox    → Receive incoming activities
#   GET  /users/{username}/followers → Followers collection
#   GET  /inbox                     → Shared inbox (delivery optimisation)
#
# Content negotiation:
#   AP clients send Accept: application/activity+json
#   Browsers send Accept: text/html
#   We serve AP JSON for the former, redirect to HTML for the latter.
#
# Security:
#   - Incoming activities are verified via HTTP Signatures
#   - Only Accept/Reject Follow for now; other types are queued
#     to Celery for async processing (to keep inbox responses fast)

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func as sql_func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ap.builder import (
    build_accept_activity,
    build_actor,
    build_create_activity,
    build_followers_collection,
    build_outbox_collection,
    build_outbox_page,
    build_recipe_article,
)
from app.ap.signatures import verify_request
from app.config import settings
from app.database import get_db
from app.models.follower import Follower
from app.models.recipe import Recipe, RecipeStatus
from app.models.user import User

router = APIRouter(tags=["activitypub"])

# Content type for ActivityPub responses
AP_CONTENT_TYPE = "application/activity+json"


# ============================================================
# Helpers
# ============================================================

def _ap_response(data: dict, status_code: int = 200) -> JSONResponse:
    """Return a JSONResponse with the ActivityPub content type."""
    return JSONResponse(content=data, status_code=status_code, media_type=AP_CONTENT_TYPE)


async def _get_local_user(username: str, db: AsyncSession) -> User:
    """Fetch a local (non-remote) user by username or raise 404."""
    result = await db.execute(
        select(User).where(User.username == username, User.is_remote == False)  # noqa: E712
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _fetch_remote_actor(actor_url: str) -> dict | None:
    """
    Fetch an ActivityPub Actor from a remote server.

    Used to retrieve the public key of the sender when verifying
    an incoming HTTP Signature.

    Returns the actor dict or None if the fetch fails.
    We use a short timeout — if the remote server is slow,
    we reject the request rather than blocking our inbox.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                actor_url,
                headers={"Accept": "application/activity+json"},
                follow_redirects=True,
            )
            if response.status_code == 200:
                return response.json()
    except Exception:
        pass
    return None


# ============================================================
# Actor endpoint
# ============================================================

@router.get("/users/{username}")
async def get_actor(
    username: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Return the ActivityPub Actor profile for a local user.

    Content negotiation:
    - AP clients (Mastodon) send Accept: application/activity+json
      → we return the AP JSON
    - Browsers send Accept: text/html
      → in future we'll redirect to the HTML profile page;
        for now we return the AP JSON for everyone
    """
    user = await _get_local_user(username, db)
    actor = build_actor(user, settings.instance_domain)
    return _ap_response(actor)


# ============================================================
# Outbox
# ============================================================

@router.get("/users/{username}/outbox")
async def get_outbox(
    username: str,
    page: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Return a user's published activities.

    Without ?page: returns the OrderedCollection root (just the count
    and a pointer to page 1). This is what AP clients fetch first.

    With ?page=N: returns an OrderedCollectionPage with actual activities.
    """
    user = await _get_local_user(username, db)
    actor_url = f"https://{settings.instance_domain}/users/{username}"

    # Count total published recipes
    count_result = await db.execute(
        select(sql_func.count(Recipe.id)).where(
            Recipe.author_id == user.id,
            Recipe.status == RecipeStatus.PUBLISHED,
        )
    )
    total = count_result.scalar_one()

    # Without ?page, return just the collection envelope
    if page is None:
        return _ap_response(build_outbox_collection(actor_url, total))

    # With ?page=N, return the actual activities
    per_page = 20
    offset = (page - 1) * per_page

    recipes_result = await db.execute(
        select(Recipe)
        .where(
            Recipe.author_id == user.id,
            Recipe.status == RecipeStatus.PUBLISHED,
        )
        .options(
            selectinload(Recipe.author),
            selectinload(Recipe.translations),
            selectinload(Recipe.ingredients),
            selectinload(Recipe.photos),
        )
        .order_by(Recipe.published_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    recipes = recipes_result.scalars().all()

    # Build Create{Article} activities for each recipe.
    # We use the first (original language) translation for the AP object.
    activities = []
    for recipe in recipes:
        if not recipe.translations:
            continue
        # Prefer the translation matching the user's preferred language,
        # fall back to the first available translation
        translation = next(
            (t for t in recipe.translations if t.language == user.preferred_language),
            recipe.translations[0],
        )
        article = build_recipe_article(recipe, translation, settings.instance_domain)
        activities.append(build_create_activity(actor_url, article))

    return _ap_response(
        build_outbox_page(actor_url, activities, total, page, per_page)
    )


# ============================================================
# Followers collection
# ============================================================

@router.get("/users/{username}/followers")
async def get_followers(
    username: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Return the followers collection for a user.

    We return only the count, not the actual list of follower AP IDs.
    This is a deliberate privacy choice: enumerating followers would
    expose who follows whom to any server that asks.
    Mastodon does the same.
    """
    user = await _get_local_user(username, db)
    actor_url = f"https://{settings.instance_domain}/users/{username}"

    count_result = await db.execute(
        select(sql_func.count()).where(Follower.followee_id == user.id)
    )
    total = count_result.scalar_one()

    return _ap_response(build_followers_collection(actor_url, total))


# ============================================================
# Inbox
# ============================================================

@router.post("/users/{username}/inbox", status_code=202)
async def user_inbox(
    username: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Receive an ActivityPub activity directed at a local user.

    We handle these activity types synchronously (they're fast):
      - Follow   → store follower, send Accept back
      - Undo{Follow} → remove follower

    Everything else (Like, Announce, Create, Update, Delete from
    remote) is acknowledged with 202 Accepted and would be queued
    to Celery for async processing in a future iteration.

    HTTP Signature verification:
    We verify the signature on all incoming POST requests.
    If the signature is invalid or the sender's key can't be
    fetched, we return 401.
    """
    user = await _get_local_user(username, db)

    # --- Parse body ---
    try:
        body = await request.body()
        activity = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # --- Verify HTTP Signature ---
    # Get all headers as a lowercased dict for signature verification
    headers_lower = {k.lower(): v for k, v in request.headers.items()}

    actor_url = activity.get("actor")
    if not actor_url:
        raise HTTPException(status_code=400, detail="Missing actor in activity")

    # Fetch the remote actor to get their public key
    remote_actor = await _fetch_remote_actor(actor_url)
    if remote_actor is None:
        raise HTTPException(status_code=401, detail="Could not fetch actor for signature verification")

    public_key_pem = (
        remote_actor.get("publicKey", {}).get("publicKeyPem")
        if isinstance(remote_actor.get("publicKey"), dict)
        else None
    )
    if not public_key_pem:
        raise HTTPException(status_code=401, detail="Actor has no public key")

    path = request.url.path
    if request.url.query:
        path += f"?{request.url.query}"

    if not verify_request("post", path, headers_lower, public_key_pem):
        raise HTTPException(status_code=401, detail="Invalid HTTP Signature")

    # --- Dispatch by activity type ---
    activity_type = activity.get("type", "")

    if activity_type == "Follow":
        await _handle_follow(user, activity, remote_actor, db)

    elif activity_type == "Undo":
        obj = activity.get("object", {})
        if isinstance(obj, dict) and obj.get("type") == "Follow":
            await _handle_unfollow(user, actor_url, db)
        # Other Undo types (Like, Announce) — acknowledge and ignore for now

    # All other types: 202 Accepted, process later
    # (Like, Announce, Create, Update, Delete from remote servers)

    return {"status": "accepted"}


@router.post("/inbox", status_code=202)
async def shared_inbox(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Shared inbox endpoint for efficient delivery.

    Large Fediverse servers (Mastodon instances with many users)
    use the shared inbox to deliver a single copy of an activity
    that targets multiple local users, instead of POSTing to each
    user's inbox individually.

    For now we just acknowledge and return 202. Full shared inbox
    processing will be implemented when we add Celery task routing.
    """
    return {"status": "accepted"}


# ============================================================
# Activity handlers
# ============================================================

async def _handle_follow(
    followee: User,
    activity: dict,
    remote_actor: dict,
    db: AsyncSession,
) -> None:
    """
    Process a Follow activity.

    1. Store the follower in our database
    2. Send an Accept{Follow} back to the remote actor's inbox
    """
    actor_url = activity.get("actor")

    # Get the remote actor's inbox URL
    # Prefer sharedInbox for efficiency, fall back to personal inbox
    endpoints = remote_actor.get("endpoints", {})
    inbox_url = (
        endpoints.get("sharedInbox")
        or remote_actor.get("inbox")
    )
    if not inbox_url:
        return  # Can't send Accept without an inbox URL

    # Upsert the follower (ignore if already following)
    existing = await db.execute(
        select(Follower).where(
            Follower.followee_id == followee.id,
            Follower.follower_ap_id == actor_url,
        )
    )
    if existing.scalar_one_or_none() is None:
        follower = Follower(
            followee_id=followee.id,
            follower_ap_id=actor_url,
            follower_inbox=inbox_url,
        )
        db.add(follower)
        await db.flush()

    # Send Accept{Follow} back
    actor_url_local = f"https://{settings.instance_domain}/users/{followee.username}"
    accept = build_accept_activity(actor_url_local, activity)

    await _deliver_activity(
        activity=accept,
        inbox_url=inbox_url,
        sender=followee,
    )


async def _handle_unfollow(
    followee: User,
    follower_ap_id: str,
    db: AsyncSession,
) -> None:
    """Remove a follower from the database."""
    result = await db.execute(
        select(Follower).where(
            Follower.followee_id == followee.id,
            Follower.follower_ap_id == follower_ap_id,
        )
    )
    follower = result.scalar_one_or_none()
    if follower:
        await db.delete(follower)
        await db.flush()


async def _deliver_activity(
    activity: dict,
    inbox_url: str,
    sender: User,
) -> None:
    """
    Deliver an ActivityPub activity to a remote inbox via HTTP POST.

    Signs the request with the sender's private key so the receiver
    can verify it really came from us.

    This is called synchronously for Accept{Follow} because the
    remote server is waiting for it. For bulk delivery (new recipes
    to all followers) we'll use Celery tasks instead.
    """
    from app.ap.signatures import sign_request

    if not sender.private_key:
        return  # Local user with no key — shouldn't happen but be safe

    key_id = f"https://{settings.instance_domain}/users/{sender.username}#main-key"
    body = json.dumps(activity).encode("utf-8")

    headers = sign_request(
        method="post",
        url=inbox_url,
        body=body,
        private_key_pem=sender.private_key,
        key_id=key_id,
    )
    headers["Content-Type"] = AP_CONTENT_TYPE

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(inbox_url, content=body, headers=headers)
    except Exception:
        # Delivery failure is not fatal — in production we'd retry via Celery
        pass
