# ============================================================
# app/routers/dashboard.py — notifications, feed, personal cookbook
# ============================================================
#
# Routes:
#   GET /notifications                      — follow requests + activity
#   GET /feed                               — recipes from people I follow
#   GET /my-recipes                         — own recipes + bookmarks
#   GET /api/v1/notifications/unread-count  — badge count for navbar JS

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.follow_request import FollowRequest, FollowRequestStatus
from app.models.follower import Follower
from app.models.moderation import Bookmark
from app.models.notification import Notification, NotificationType
from app.models.recipe import Recipe, RecipeStatus
from app.models.user import User
from app.templates_env import templates

router = APIRouter(tags=["frontend"])


# ============================================================
# Helper: create a notification (imported by other routers)
# ============================================================

async def create_notification(
    db: AsyncSession,
    recipient_id: uuid.UUID,
    notification_type: NotificationType,
    actor_ap_id: str,
    actor_display_name: str | None = None,
    object_id: str | None = None,
    summary: str | None = None,
) -> None:
    """
    Insert a notification row. Silently ignores errors so that
    a notification failure never breaks the main action.
    """
    try:
        notif = Notification(
            recipient_id=recipient_id,
            notification_type=notification_type,
            actor_ap_id=actor_ap_id,
            actor_display_name=actor_display_name,
            object_id=object_id,
            summary=summary,
        )
        db.add(notif)
        await db.flush()
    except Exception:
        pass


# ============================================================
# GET /notifications
# ============================================================

@router.get("/notifications")
async def notifications_page(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Unified notification page.
    Top section: pending follow requests (Accept / Reject buttons).
    Bottom section: activity notifications (new comments, etc.).
    Marks activity notifications as read on visit.
    """
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    # --- Pending follow requests ---
    req_result = await db.execute(
        select(FollowRequest)
        .where(
            FollowRequest.followee_id == current_user.id,
            FollowRequest.status == FollowRequestStatus.PENDING,
        )
        .order_by(FollowRequest.created_at.desc())
    )
    pending_reqs = req_result.scalars().all()

    requests_data = []
    for req in pending_reqs:
        if req.is_local and req.requester_id:
            user_result = await db.execute(
                select(User).where(User.id == req.requester_id)
            )
            requester = user_result.scalar_one_or_none()
            requests_data.append({
                "id": str(req.id),
                "actor_ap_id": req.actor_ap_id,
                "username": requester.username if requester else None,
                "display_name": requester.display_name if requester else None,
                "profile_url": f"/users/{requester.username}" if requester else req.actor_ap_id,
                "created_at": req.created_at.strftime("%Y-%m-%d"),
                "is_local": True,
            })
        else:
            handle = req.actor_ap_id.split("/users/")[-1] if "/users/" in req.actor_ap_id else req.actor_ap_id
            domain = req.actor_ap_id.split("/")[2] if req.actor_ap_id.startswith("https://") else ""
            requests_data.append({
                "id": str(req.id),
                "actor_ap_id": req.actor_ap_id,
                "username": handle,
                "display_name": f"@{handle}@{domain}",
                "profile_url": req.actor_ap_id,
                "created_at": req.created_at.strftime("%Y-%m-%d"),
                "is_local": False,
            })

    # --- Activity notifications (unread only) ---
    # Load only unread notifications — they disappear after this visit
    # because we mark them as read immediately after loading.
    notif_result = await db.execute(
        select(Notification)
        .where(
            Notification.recipient_id == current_user.id,
            Notification.read_at.is_(None),
        )
        .order_by(Notification.created_at.desc())
        .limit(50)
    )
    notifications_raw = notif_result.scalars().all()

    # Mark all as read now — next visit the list will be empty
    # until new notifications arrive.
    if notifications_raw:
        await db.execute(
            update(Notification)
            .where(
                Notification.recipient_id == current_user.id,
                Notification.read_at.is_(None),
            )
            .values(read_at=datetime.now(timezone.utc))
        )
        await db.flush()

    notifications = []
    for n in notifications_raw:
        link = None
        if n.notification_type == NotificationType.NEW_COMMENT and n.object_id:
            link = f"/api/v1/recipes/{n.object_id}"

        notifications.append({
            "id": str(n.id),
            "type": n.notification_type,
            "actor_display_name": n.actor_display_name or n.actor_ap_id,
            "summary": n.summary,
            "link": link,
            "created_at": n.created_at.strftime("%Y-%m-%d %H:%M"),
            "is_unread": True,  # all shown are unread by definition
        })

    return templates.TemplateResponse("notifications.html", {
        "request": request,
        "current_user": current_user,
        "pending_requests": requests_data,
        "notifications": notifications,
    })


# ============================================================
# GET /api/v1/notifications/unread-count
# ============================================================

@router.get("/api/v1/notifications/unread-count")
async def unread_notification_count(
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Return total unread count for navbar badge (activity + follow requests).
    Called by the JS snippet in base.html after page load.
    """
    if not current_user:
        return JSONResponse({"count": 0})

    notif_result = await db.execute(
        select(func.count()).where(
            Notification.recipient_id == current_user.id,
            Notification.read_at.is_(None),
        )
    )
    req_result = await db.execute(
        select(func.count()).where(
            FollowRequest.followee_id == current_user.id,
            FollowRequest.status == FollowRequestStatus.PENDING,
        )
    )
    total = (notif_result.scalar() or 0) + (req_result.scalar() or 0)
    return JSONResponse({"count": total})


# ============================================================
# GET /feed
# ============================================================

@router.get("/feed")
async def feed_page(
    request: Request,
    page: int = Query(default=1, ge=1),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Recipes from people the current user follows, newest first."""
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    per_page = 20

    followers_result = await db.execute(
        select(Follower.followee_id).where(
            Follower.follower_ap_id == current_user.ap_id
        )
    )
    followed_ids = [row[0] for row in followers_result.fetchall()]

    recipes = []
    has_more = False

    if followed_ids:
        result = await db.execute(
            select(Recipe)
            .where(
                Recipe.author_id.in_(followed_ids),
                Recipe.status == RecipeStatus.PUBLISHED,
            )
            .options(
                selectinload(Recipe.author),
                selectinload(Recipe.translations),
                selectinload(Recipe.photos),
            )
            .order_by(Recipe.published_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page + 1)
        )
        all_recipes = result.scalars().all()
        has_more = len(all_recipes) > per_page

        for recipe in all_recipes[:per_page]:
            translation = next(
                (t for t in recipe.translations if t.language == recipe.original_language),
                recipe.translations[0] if recipe.translations else None,
            )
            cover = next((p for p in recipe.photos if p.is_cover), None) or (
                recipe.photos[0] if recipe.photos else None
            )
            recipes.append({
                "id": str(recipe.id),
                "title": translation.title if translation else recipe.slug,
                "description": translation.description if translation else None,
                "author_username": recipe.author.username,
                "author_display": recipe.author.display_name or recipe.author.username,
                "published_at": recipe.published_at.strftime("%Y-%m-%d") if recipe.published_at else None,
                "dietary_tags": recipe.dietary_tags or [],
                "cover_url": (
                    f"https://{settings.instance_domain}/media/{cover.url}"
                    if cover else None
                ),
            })

    return templates.TemplateResponse("feed.html", {
        "request": request,
        "current_user": current_user,
        "recipes": recipes,
        "page": page,
        "has_more": has_more,
        "following_count": len(followed_ids),
    })


# ============================================================
# GET /my-recipes
# ============================================================

@router.get("/my-recipes")
async def my_recipes_page(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Personal cookbook: own recipes + bookmarks."""
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    own_result = await db.execute(
        select(Recipe)
        .where(
            Recipe.author_id == current_user.id,
            Recipe.status.in_([RecipeStatus.PUBLISHED, RecipeStatus.DRAFT]),
        )
        .options(selectinload(Recipe.translations), selectinload(Recipe.photos))
        .order_by(Recipe.created_at.desc())
    )
    own_recipes_raw = own_result.scalars().all()

    own_recipes = []
    for recipe in own_recipes_raw:
        translation = next(
            (t for t in recipe.translations if t.language == recipe.original_language),
            recipe.translations[0] if recipe.translations else None,
        )
        cover = next((p for p in recipe.photos if p.is_cover), None) or (
            recipe.photos[0] if recipe.photos else None
        )
        own_recipes.append({
            "id": str(recipe.id),
            "title": translation.title if translation else recipe.slug,
            "status": recipe.status.value,
            "published_at": recipe.published_at.strftime("%Y-%m-%d") if recipe.published_at else None,
            "dietary_tags": recipe.dietary_tags or [],
            "cover_url": (
                f"https://{settings.instance_domain}/media/{cover.url}"
                if cover else None
            ),
        })

    bm_result = await db.execute(
        select(Bookmark)
        .where(Bookmark.user_id == current_user.id)
        .order_by(Bookmark.created_at.desc())
    )
    bookmarks_raw = bm_result.scalars().all()

    bookmarked = []
    for bm in bookmarks_raw:
        title = bm.title
        cover_url = None

        if bm.recipe_ap_id.startswith(f"https://{settings.instance_domain}/"):
            local_result = await db.execute(
                select(Recipe)
                .where(Recipe.ap_id == bm.recipe_ap_id)
                .options(selectinload(Recipe.translations), selectinload(Recipe.photos))
            )
            local_recipe = local_result.scalar_one_or_none()
            if local_recipe:
                trans = next(
                    (t for t in local_recipe.translations
                     if t.language == local_recipe.original_language),
                    local_recipe.translations[0] if local_recipe.translations else None,
                )
                if trans and not title:
                    title = trans.title
                cover = next((p for p in local_recipe.photos if p.is_cover), None) or (
                    local_recipe.photos[0] if local_recipe.photos else None
                )
                if cover:
                    cover_url = f"https://{settings.instance_domain}/media/{cover.url}"

        bookmarked.append({
            "id": str(bm.id),
            "recipe_ap_id": bm.recipe_ap_id,
            "title": title or bm.recipe_ap_id,
            "author_name": bm.author_name,
            "cover_url": cover_url,
            "saved_at": bm.created_at.strftime("%Y-%m-%d"),
            "link": bm.recipe_ap_id if not bm.recipe_ap_id.startswith(
                f"https://{settings.instance_domain}/"
            ) else f"/api/v1/recipes/{bm.recipe_ap_id.split('/')[-1]}",
        })

    return templates.TemplateResponse("my_recipes.html", {
        "request": request,
        "current_user": current_user,
        "own_recipes": own_recipes,
        "bookmarked": bookmarked,
        "draft_count": sum(1 for r in own_recipes if r["status"] == "draft"),
        "published_count": sum(1 for r in own_recipes if r["status"] == "published"),
    })
