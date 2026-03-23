# ============================================================
# app/routers/activitypub.py — ActivityPub endpoints
# ============================================================

import json
import logging
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
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
from app.models.cooked_this import CookedThis, CookedThisStatus
from app.models.follower import Follower
from app.models.reaction import Reaction, ReactionType
from app.models.recipe import Recipe, RecipeStatus
from app.models.user import User
from app.ap.ratelimit import check_rate_limit
from fastapi.templating import Jinja2Templates
from fastapi import Request as FastAPIRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["activitypub"])

AP_CONTENT_TYPE = "application/activity+json"
PAGE_SIZE = 20

templates = Jinja2Templates(directory="app/templates")

# ============================================================
# Helpers
# ============================================================

async def _fetch_remote_actor(actor_url: str) -> dict | None:
    """Fetch a remote AP actor and return its JSON, or None on failure."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                actor_url,
                headers={"Accept": AP_CONTENT_TYPE},
                follow_redirects=True,
            )
            if resp.status_code == 200:
                return resp.json()
    except httpx.RequestError as exc:
        logger.warning("Failed to fetch remote actor %s: %s", actor_url, exc)
    return None


async def _deliver_activity(inbox_url: str, activity: dict, sender: User) -> None:
    """Sign and POST an AP activity to a remote inbox."""
    from app.ap.signatures import sign_request
    body = json.dumps(activity).encode("utf-8")
    key_id = f"{sender.ap_id}#main-key"
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
    except httpx.RequestError as exc:
        logger.warning("Failed to deliver to %s: %s", inbox_url, exc)


async def _get_local_user(username: str, db: AsyncSession) -> User:
    """Return a local User or raise 404."""
    result = await db.execute(
        select(User).where(User.username == username, User.is_remote.is_(False))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ============================================================
# Actor
# ============================================================

@router.get("/users/{username}")
async def get_actor(
    username: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Return the AP Actor profile for a local user.
    Content negotiation:
      - Accept: text/html            → HTML profile page (for browsers)
      - Accept: application/activity+json → AP Actor JSON (for federation)
    """
    user = await _get_local_user(username, db)

    accept = request.headers.get("accept", "")

    if "text/html" in accept and "application/activity+json" not in accept:
        from app.models.recipe import RecipeTranslation
        recipes_result = await db.execute(
            select(Recipe)
            .where(
                Recipe.author_id == user.id,
                Recipe.status == RecipeStatus.PUBLISHED,
            )
            .order_by(Recipe.published_at.desc())
            .limit(10)
        )
        recipes = recipes_result.scalars().all()

        recipe_list = []
        for recipe in recipes:
            trans_result = await db.execute(
                select(RecipeTranslation).where(
                    RecipeTranslation.recipe_id == recipe.id,
                    RecipeTranslation.language == recipe.original_language,
                ).limit(1)
            )
            translation = trans_result.scalar_one_or_none()
            recipe_list.append({
                "id": str(recipe.id),
                "slug": recipe.slug,
                "title": translation.title if translation else None,
                "published_at": recipe.published_at.isoformat() if recipe.published_at else None,
            })

        return templates.TemplateResponse(
            "user_profile.html",
            {
                "request": request,
                "user": user,
                "recipes": recipe_list,
                "instance_domain": settings.instance_domain,
            },
        )

    # Default: AP JSON for federation clients
    actor = build_actor(user, settings.instance_domain)
    return Response(content=json.dumps(actor), media_type=AP_CONTENT_TYPE)



# ============================================================
# Outbox
# ============================================================

@router.get("/users/{username}/outbox")
async def get_outbox(
    username: str,
    page: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Return an OrderedCollection of Create{Article} activities."""
    user = await _get_local_user(username, db)
    outbox_url = f"https://{settings.instance_domain}/users/{username}/outbox"

    count_result = await db.execute(
        select(func.count()).where(
            Recipe.author_id == user.id,
            Recipe.status == RecipeStatus.PUBLISHED,
        )
    )
    total = count_result.scalar() or 0

    if page is None:
        collection = build_outbox_collection(outbox_url, total)
        return Response(content=json.dumps(collection), media_type=AP_CONTENT_TYPE)

    result = await db.execute(
        select(Recipe)
        .where(Recipe.author_id == user.id, Recipe.status == RecipeStatus.PUBLISHED)
        .options(selectinload(Recipe.translations), selectinload(Recipe.photos))
        .order_by(Recipe.published_at.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    recipes = result.scalars().all()

    activities = []
    for recipe in recipes:
        translation = next(
            (t for t in recipe.translations if t.language == user.preferred_language),
            recipe.translations[0] if recipe.translations else None,
        )
        if not translation:
            continue
        article = build_recipe_article(recipe, translation, settings.instance_domain)
        actor_url = f"https://{settings.instance_domain}/users/{username}"
        activities.append(build_create_activity(actor_url, article))

    collection_page = build_outbox_page(outbox_url, activities, total, page, PAGE_SIZE)
    return Response(content=json.dumps(collection_page), media_type=AP_CONTENT_TYPE)


# ============================================================
# Followers
# ============================================================

@router.get("/users/{username}/followers")
async def get_followers(username: str, db: AsyncSession = Depends(get_db)):
    """Return follower count only (no enumeration for privacy)."""
    user = await _get_local_user(username, db)
    followers_url = f"https://{settings.instance_domain}/users/{username}/followers"

    count_result = await db.execute(
        select(func.count()).where(Follower.followee_id == user.id)
    )
    total = count_result.scalar() or 0

    collection = build_followers_collection(followers_url, total)
    return Response(content=json.dumps(collection), media_type=AP_CONTENT_TYPE)


# ============================================================
# Inbox
# ============================================================

@router.post("/users/{username}/inbox", status_code=202)
async def inbox(
    username: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Receive an AP activity from a remote server.
    Handles: Follow, Undo, Like, Announce, Create{Note}.
    """
    user = await _get_local_user(username, db)
    body = await request.body()
    
    try:
        raw = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
            
    # Check rate limits before processing the activity
    client_ip = request.client.host if request.client else "unknown"
    actor_url_for_limit = raw.get("actor", "unknown")
    allowed, reason = await check_rate_limit(client_ip, actor_url_for_limit)
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)
    
    from app.ap.instances import record_instance
    await record_instance(raw.get("actor", ""), db)    
        
    actor_url = raw.get("actor")
    if not actor_url:
        raise HTTPException(status_code=400, detail="Missing actor field")

    remote_actor = await _fetch_remote_actor(actor_url)
    if not remote_actor:
        raise HTTPException(status_code=400, detail="Could not fetch remote actor")

    public_key_pem = remote_actor.get("publicKey", {}).get("publicKeyPem", "")
    if not verify_request(
        method=request.method.lower(),
        path=str(request.url.path),
        headers=dict(request.headers),
        public_key_pem=public_key_pem,
    ):
        raise HTTPException(status_code=401, detail="Invalid HTTP Signature")

    activity_type = raw.get("type", "")

    # ----------------------------------------------------------
    # Follow
    # ----------------------------------------------------------
    if activity_type == "Follow":
        inbox_url = remote_actor.get("inbox", "")
        follower = Follower(
            followee_id=user.id,
            follower_ap_id=actor_url,
            follower_inbox=inbox_url,
        )
        try:
            db.add(follower)
            await db.flush()
        except IntegrityError:
            await db.rollback()

        actor_self_url = f"https://{settings.instance_domain}/users/{username}"
        accept = build_accept_activity(actor_self_url, raw)
        await _deliver_activity(inbox_url, accept, user)
        return Response(status_code=202)

    # ----------------------------------------------------------
    # Undo
    # ----------------------------------------------------------
    if activity_type == "Undo":
        obj = raw.get("object", {})
        obj_type = obj.get("type") if isinstance(obj, dict) else None

        if obj_type == "Follow":
            result = await db.execute(
                select(Follower).where(
                    Follower.followee_id == user.id,
                    Follower.follower_ap_id == actor_url,
                )
            )
            follower = result.scalar_one_or_none()
            if follower:
                await db.delete(follower)
                await db.flush()

        elif obj_type in ("Like", "Announce"):
            reaction_type = ReactionType.LIKE if obj_type == "Like" else ReactionType.ANNOUNCE
            result = await db.execute(
                select(Reaction).where(
                    Reaction.actor_ap_id == actor_url,
                    Reaction.reaction_type == reaction_type,
                )
            )
            reaction = result.scalar_one_or_none()
            if reaction:
                await db.delete(reaction)
                await db.flush()

        return Response(status_code=202)

    # ----------------------------------------------------------
    # Like
    # ----------------------------------------------------------
    if activity_type == "Like":
        obj = raw.get("object")
        if not isinstance(obj, str):
            obj = obj.get("id") if isinstance(obj, dict) else None
        if obj:
            result = await db.execute(select(Recipe).where(Recipe.ap_id == obj))
            recipe = result.scalar_one_or_none()
            if recipe:
                reaction = Reaction(
                    recipe_id=recipe.id,
                    actor_ap_id=actor_url,
                    reaction_type=ReactionType.LIKE,
                    activity_ap_id=raw.get("id"),
                )
                try:
                    db.add(reaction)
                    await db.flush()
                except IntegrityError:
                    await db.rollback()
        return Response(status_code=202)

    # ----------------------------------------------------------
    # Announce
    # ----------------------------------------------------------
    if activity_type == "Announce":
        obj = raw.get("object")
        if not isinstance(obj, str):
            obj = obj.get("id") if isinstance(obj, dict) else None
        if obj:
            result = await db.execute(select(Recipe).where(Recipe.ap_id == obj))
            recipe = result.scalar_one_or_none()
            if recipe:
                reaction = Reaction(
                    recipe_id=recipe.id,
                    actor_ap_id=actor_url,
                    reaction_type=ReactionType.ANNOUNCE,
                    activity_ap_id=raw.get("id"),
                )
                try:
                    db.add(reaction)
                    await db.flush()
                except IntegrityError:
                    await db.rollback()
        return Response(status_code=202)

    # ----------------------------------------------------------
    # Create{Note} — incoming comment / CookedThis
    # ----------------------------------------------------------
    if activity_type == "Create":
        obj = raw.get("object", {})
        if not isinstance(obj, dict) or obj.get("type") != "Note":
            return Response(status_code=202)

        in_reply_to = obj.get("inReplyTo")
        if not in_reply_to:
            return Response(status_code=202)

        # Strip HTML tags — store plain text only
        raw_content = obj.get("content", "") or obj.get("name", "") or ""
        plain_content = re.sub(r"<[^>]+>", "", raw_content).strip()
        if not plain_content:
            return Response(status_code=202)

        moderation_on = settings.comments_moderation == "on"
        initial_status = (
            CookedThisStatus.PENDING if moderation_on
            else CookedThisStatus.PUBLISHED
        )

        # Find what this Note is replying to
        recipe_id = None
        parent_id = None

        recipe_result = await db.execute(
            select(Recipe).where(Recipe.ap_id == in_reply_to)
        )
        recipe = recipe_result.scalar_one_or_none()

        if recipe:
            recipe_id = recipe.id
        else:
            # Maybe it replies to another local comment
            parent_result = await db.execute(
                select(CookedThis).where(CookedThis.ap_id == in_reply_to)
            )
            parent_comment = parent_result.scalar_one_or_none()
            if parent_comment:
                recipe_id = parent_comment.recipe_id
                parent_id = parent_comment.id
            else:
                # Not a reply to our content — ignore
                return Response(status_code=202)

        note_ap_id = obj.get("id")

        # Deduplicate
        if note_ap_id:
            existing = await db.execute(
                select(CookedThis).where(CookedThis.ap_id == note_ap_id)
            )
            if existing.scalar_one_or_none():
                return Response(status_code=202)

        comment = CookedThis(
            recipe_id=recipe_id,
            actor_ap_id=actor_url,
            ap_id=note_ap_id,
            in_reply_to=in_reply_to,
            parent_id=parent_id,
            content=plain_content,
            is_remote=True,
            status=initial_status,
        )
        db.add(comment)
        await db.flush()
        return Response(status_code=202)

    # ----------------------------------------------------------
    # Update{Note} or Update{Article}
    # ----------------------------------------------------------
    if activity_type == "Update":
        obj = raw.get("object", {})
        if not isinstance(obj, dict):
            return Response(status_code=202)

        obj_type = obj.get("type")

        if obj_type == "Note":
            note_ap_id = obj.get("id")
            if not note_ap_id:
                return Response(status_code=202)

            raw_content = obj.get("content", "") or obj.get("name", "") or ""
            plain_content = re.sub(r"<[^>]+>", "", raw_content).strip()
            if not plain_content:
                return Response(status_code=202)

            result = await db.execute(
                select(CookedThis).where(
                    CookedThis.ap_id == note_ap_id,
                    CookedThis.actor_ap_id == actor_url,
                )
            )
            comment = result.scalar_one_or_none()
            if comment:
                comment.content = plain_content
                await db.flush()

        elif obj_type == "Article":
            # We do not auto-update forks — user owns their fork.
            # Just acknowledge.
            pass

        return Response(status_code=202)

    # ----------------------------------------------------------
    # Delete{Note} or Delete{Article}
    # ----------------------------------------------------------
    if activity_type == "Delete":
        obj = raw.get("object")

        if isinstance(obj, str):
            obj_id = obj
            obj_type = None
        elif isinstance(obj, dict):
            obj_id = obj.get("id")
            obj_type = obj.get("type")
        else:
            return Response(status_code=202)

        if not obj_id:
            return Response(status_code=202)

        if obj_type == "Article" or (obj_type is None and obj_id.startswith(f"https://{settings.instance_domain}")):
            # Try to delete a local recipe
            result = await db.execute(
                select(Recipe).where(
                    Recipe.ap_id == obj_id,
                    Recipe.status != RecipeStatus.DELETED,
                )
            )
            recipe = result.scalar_one_or_none()
            if recipe:
                author_result = await db.execute(
                    select(User).where(User.id == recipe.author_id)
                )
                author = author_result.scalar_one_or_none()
                if author and author.ap_id == actor_url:
                    recipe.status = RecipeStatus.DELETED
                    await db.flush()
        else:
            # Try to delete a local comment (Note)
            result = await db.execute(
                select(CookedThis).where(
                    CookedThis.ap_id == obj_id,
                    CookedThis.actor_ap_id == actor_url,
                )
            )
            comment = result.scalar_one_or_none()
            if comment:
                await db.delete(comment)
                await db.flush()

        return Response(status_code=202)

    # ----------------------------------------------------------
    # Update{Article} — remote server updated a recipe
    # ----------------------------------------------------------
    if activity_type == "Update":
        obj = raw.get("object", {})
        if not isinstance(obj, dict) or obj.get("type") != "Article":
            # Update{Note} is handled above — ignore other types
            return Response(status_code=202)

        article_ap_id = obj.get("id")
        if not article_ap_id:
            return Response(status_code=202)

        # Only update recipes that were forked from this AP ID
        # We do not store remote recipes directly — only forks
        result = await db.execute(
            select(Recipe).where(Recipe.forked_from == article_ap_id)
        )
        # We intentionally do NOT auto-update forks — the user owns
        # their fork and may have made changes. Just acknowledge.
        # If in future we want to notify the user, this is the place.
        return Response(status_code=202)

    # ----------------------------------------------------------
    # Delete{Article} — remote server deleted a recipe
    # ----------------------------------------------------------
    if activity_type == "Delete":
        obj = raw.get("object")

        if isinstance(obj, str):
            article_ap_id = obj
        elif isinstance(obj, dict):
            article_ap_id = obj.get("id")
        else:
            return Response(status_code=202)

        if not article_ap_id:
            return Response(status_code=202)

        # Delete local recipe if it matches the AP ID and the
        # actor is the author (prevent unauthorized deletions)
        result = await db.execute(
            select(Recipe).where(
                Recipe.ap_id == article_ap_id,
                Recipe.status != RecipeStatus.DELETED,
            )
        )
        recipe = result.scalar_one_or_none()
        if recipe:
            # Verify the actor is the recipe author
            author_ap_id = f"https://{settings.instance_domain}/users/{recipe.author.username if hasattr(recipe, 'author') else ''}"
            # Load author to check
            author_result = await db.execute(
                select(User).where(User.id == recipe.author_id)
            )
            author = author_result.scalar_one_or_none()
            if author and author.ap_id == actor_url:
                recipe.status = RecipeStatus.DELETED
                await db.flush()

        return Response(status_code=202)


# ============================================================
# Shared inbox
# ============================================================

@router.post("/inbox", status_code=202)
async def shared_inbox(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Shared inbox — receives activities addressed to any local user.
    Finds the intended recipient from the activity's to/cc fields
    and delegates to the personal inbox handler.
    """
    body = await request.body()

    try:
        raw = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    actor_url_for_limit = raw.get("actor", "unknown")
    allowed, reason = await check_rate_limit(client_ip, actor_url_for_limit)
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)

    # Find the local recipient by scanning to/cc fields
    # and the object field for the AP ID of a local user
    instance_prefix = f"https://{settings.instance_domain}/users/"

    recipient_username = None

    for field in ("to", "cc"):
        value = raw.get(field, [])
        if isinstance(value, str):
            value = [value]
        for url in value:
            if isinstance(url, str) and url.startswith(instance_prefix):
                # Extract username from URL like https://instance/users/maria
                candidate = url[len(instance_prefix):].split("/")[0]
                if candidate:
                    recipient_username = candidate
                    break
        if recipient_username:
            break

    # If not found in to/cc, check object field
    if not recipient_username:
        obj = raw.get("object")
        if isinstance(obj, str) and obj.startswith(instance_prefix):
            candidate = obj[len(instance_prefix):].split("/")[0]
            if candidate:
                recipient_username = candidate
        elif isinstance(obj, dict):
            for field in ("to", "cc", "attributedTo"):
                value = obj.get(field, [])
                if isinstance(value, str):
                    value = [value]
                for url in value:
                    if isinstance(url, str) and url.startswith(instance_prefix):
                        candidate = url[len(instance_prefix):].split("/")[0]
                        if candidate:
                            recipient_username = candidate
                            break
                if recipient_username:
                    break

    if not recipient_username:
        # Cannot determine recipient — acknowledge and ignore
        logger.debug("shared_inbox: could not determine recipient for activity type %s", raw.get("type"))
        return Response(status_code=202)

    # Verify the recipient exists
    result = await db.execute(
        select(User).where(
            User.username == recipient_username,
            User.is_remote.is_(False),
        )
    )
    if not result.scalar_one_or_none():
        return Response(status_code=202)

    # Delegate to the personal inbox handler
    return await inbox(recipient_username, request, db)
