# ============================================================
# app/routers/home.py — public homepage
# ============================================================
#
# Serves the HTML homepage at GET /.
# Content-negotiates: browsers get HTML, AP clients get JSON.
#
# The homepage shows the most recently published recipes with
# pagination (20 per page). No login required.

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.recipe import Recipe, RecipePhoto, RecipeStatus
from app.models.user import User
from app.templates_env import templates

router = APIRouter(tags=["frontend"])

PAGE_SIZE = 20


@router.get("/")
async def homepage(
    request: Request,
    page: int = Query(default=1, ge=1),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Public homepage — lists the most recently published recipes.

    Content negotiation:
      - Accept: text/html (browser) → renders index.html template
      - Anything else → returns instance info as JSON

    The template receives a flat list of dicts (not ORM objects)
    so Jinja2 doesn't need to worry about lazy-loading async relationships.
    """
    accept = request.headers.get("accept", "")
    if "text/html" not in accept:
        return JSONResponse(content={
            "name": settings.instance_name,
            "description": settings.instance_description,
            "software": "pasticcio",
            "version": "0.1.0",
            "source_code": "https://github.com/oloturia/Pasticcio",
        })

    # Load published recipes, author and photos eagerly to avoid N+1 queries
    result = await db.execute(
        select(Recipe)
        .where(Recipe.status == RecipeStatus.PUBLISHED)
        .options(
            selectinload(Recipe.author),
            selectinload(Recipe.translations),
            selectinload(Recipe.photos),
        )
        .order_by(Recipe.published_at.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE + 1)   # fetch one extra to detect next page
    )
    rows = result.scalars().all()

    has_next = len(rows) > PAGE_SIZE
    recipes_page = rows[:PAGE_SIZE]

    # Build flat dicts for the template — avoids async lazy-load issues
    recipe_list = []
    for recipe in recipes_page:
        translation = next(
            (t for t in recipe.translations if t.language == recipe.original_language),
            recipe.translations[0] if recipe.translations else None,
        )
        cover = next((p for p in recipe.photos if p.is_cover), None)
        if cover is None and recipe.photos:
            cover = recipe.photos[0]

        recipe_list.append({
            "id": str(recipe.id),
            "slug": recipe.slug,
            "title": translation.title if translation else recipe.slug,
            "author_username": recipe.author.username,
            "author_display_name": recipe.author.display_name,
            "dietary_tags": recipe.dietary_tags or [],
            "prep_time_seconds": recipe.prep_time_seconds,
            "cook_time_seconds": recipe.cook_time_seconds,
            "servings": recipe.servings,
            "published_at": recipe.published_at.isoformat() if recipe.published_at else None,
            "cover_url": f"/media/{cover.url}" if cover else None,
            "cover_alt": cover.alt_text if cover else None,
        })

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "recipes": recipe_list,
            "page": page,
            "has_next": has_next,
            "instance_name": settings.instance_name,
            "current_user": current_user,
        },
    )
