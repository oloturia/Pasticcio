# ============================================================
# app/routers/users.py — public user profile endpoints
# ============================================================
#
# Endpoints:
#   GET /api/v1/users/{username}  — public profile with recent recipes

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.recipe import Recipe, RecipeStatus, RecipeTranslation
from app.models.user import User

router = APIRouter(prefix="/api/v1/users", tags=["users"])

# Number of recent recipes to include in the profile response
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
    # Title from the first available translation
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
# Endpoint
# ============================================================

@router.get("/{username}", response_model=UserProfileOut)
async def get_user_profile(
    username: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Return the public profile of a local user, including their
    most recently published recipes (up to PROFILE_RECIPES_LIMIT).

    Remote users (is_remote=True) are not exposed via this endpoint.
    Returns 404 if the user does not exist or is remote.
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

    # Load recent published recipes with their translations
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

    # Build recipe summaries — pick title from preferred or first translation
    recipe_summaries = []
    for recipe in recipes:
        # Try original language first, then fall back to first available
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
