# ============================================================
# app/routers/users.py — public user profile endpoints
# ============================================================
#
# Endpoints:
#   GET /api/v1/users/{username}  — public profile (JSON for API)
#   GET /users/{username}         — public profile (HTML for browsers,
#                                   handled in activitypub.py via content
#                                   negotiation — this router serves JSON only)
#
# The HTML profile page is rendered by activitypub.py when the browser
# sends Accept: text/html. This router only serves the JSON API response
# used by programmatic clients.
#
# The /users/{username} HTML route also needs follow status and follower
# count — those are computed here and passed to the template via
# activitypub.py. To avoid circular imports, the helper functions are
# defined here and imported there.

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.follow_request import FollowRequest, FollowRequestStatus
from app.models.follower import Follower
from app.models.recipe import Recipe, RecipeStatus
from app.models.user import User
from app.templates_env import templates

router = APIRouter(prefix="/api/v1/users", tags=["users"])

PROFILE_RECIPES_LIMIT = 10


# ============================================================
# Pydantic schemas
# ============================================================

class RecipeSummary(BaseModel):
    id: uuid.UUID
    ap_id: str
    slug: str
    original_language: str
    published_at: datetime | None
    title: str | None = None

    model_config = {"from_attributes": True}


class UserProfileOut(BaseModel):
    username: str
    display_name: str | None
    bio: str | None
    avatar_url: str | None
    ap_id: str
    recipes: list[RecipeSummary] = []

    model_config = {"from_attributes": True}


# ============================================================
# Shared helpers (also used by activitypub.py for HTML rendering)
# ============================================================

async def get_follow_status(
    current_user: User | None,
    target: User,
    db: AsyncSession,
) -> str:
    """
    Return the follow relationship status between current_user and target.
    Returns: 'following', 'pending', or 'none'.
    """
    if not current_user or current_user.id == target.id:
        return "none"

    # Check if already following
    follower_result = await db.execute(
        select(Follower).where(
            Follower.followee_id == target.id,
            Follower.follower_ap_id == current_user.ap_id,
        )
    )
    if follower_result.scalar_one_or_none():
        return "following"

    # Check if a pending request exists
    req_result = await db.execute(
        select(FollowRequest).where(
            FollowRequest.followee_id == target.id,
            FollowRequest.actor_ap_id == current_user.ap_id,
            FollowRequest.status == FollowRequestStatus.PENDING,
        )
    )
    if req_result.scalar_one_or_none():
        return "pending"

    return "none"


async def get_follower_count(user: User, db: AsyncSession) -> int:
    """Return the number of followers for a local user."""
    result = await db.execute(
        select(func.count()).where(Follower.followee_id == user.id)
    )
    return result.scalar() or 0


async def get_recipe_count(user: User, db: AsyncSession) -> int:
    """Return the number of published recipes for a local user."""
    result = await db.execute(
        select(func.count()).where(
            Recipe.author_id == user.id,
            Recipe.status == RecipeStatus.PUBLISHED,
        )
    )
    return result.scalar() or 0


# ============================================================
# HTML profile route (at /users/{username}, not /api/v1/users/)
# ============================================================
# This route lives outside the /api/v1 prefix so it can serve
# the browser-facing HTML profile at /users/{username}.
# The activitypub.py router already handles /users/{username}
# with content negotiation (HTML for browsers, JSON for AP clients).
# We add a SEPARATE route here for the /api/v1/users/{username}
# JSON endpoint used by programmatic clients.

# ============================================================
# JSON API endpoint
# ============================================================

@router.get("/{username}", response_model=UserProfileOut)
async def get_user_profile(
    username: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Return the public profile of a local user as JSON.
    Used by programmatic API clients.
    For browsers, the HTML profile is served by activitypub.py.
    """
    result = await db.execute(
        select(User).where(
            User.username == username,
            User.is_remote.is_(False),
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    recipes_result = await db.execute(
        select(Recipe)
        .where(
            Recipe.author_id == user.id,
            Recipe.status == RecipeStatus.PUBLISHED,
        )
        .options(selectinload(Recipe.translations))
        .order_by(Recipe.published_at.desc())
        .limit(PROFILE_RECIPES_LIMIT)
    )
    recipes = recipes_result.scalars().all()

    recipe_summaries = []
    for recipe in recipes:
        translation = next(
            (t for t in recipe.translations if t.language == recipe.original_language),
            recipe.translations[0] if recipe.translations else None,
        )
        recipe_summaries.append(RecipeSummary(
            id=recipe.id,
            ap_id=recipe.ap_id,
            slug=recipe.slug,
            original_language=recipe.original_language,
            published_at=recipe.published_at,
            title=translation.title if translation else None,
        ))

    return UserProfileOut(
        username=user.username,
        display_name=user.display_name,
        bio=user.bio,
        avatar_url=user.avatar_url,
        ap_id=user.ap_id,
        recipes=recipe_summaries,
    )
