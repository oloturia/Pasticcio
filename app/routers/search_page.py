# ============================================================
# app/routers/search_page.py — HTML search page
# ============================================================
#
# Serves the search page at GET /search.
# Reads query parameters from the URL (same ones used by the
# REST API at /api/v1/search/), runs the search query directly
# against the database (reusing the same logic), and renders
# the results using the search.html template.
#
# We do NOT call the REST API via HTTP — that would be wasteful.
# Instead we share the query logic by importing and calling the
# same SQLAlchemy queries used in app/routers/search.py.

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import exists, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.recipe import Recipe, RecipeIngredient, RecipeStatus, RecipeTranslation
from app.models.user import User
from app.templates_env import templates

router = APIRouter(tags=["frontend"])

PAGE_SIZE = 20


def _page_url(request: Request, page: int) -> str:
    """Build a URL for a pagination link, preserving all current query params."""
    params = dict(request.query_params)
    params["page"] = str(page)
    # Remove page=1 from URL to keep it clean
    if page == 1:
        params.pop("page", None)
    query_string = "&".join(f"{k}={v}" for k, v in params.items() if v)
    return f"/search?{query_string}" if query_string else "/search"


@router.get("/search")
async def search_page(
    request: Request,
    q: str | None = Query(default=None),
    tags: list[str] = Query(default=[]),
    language: str | None = Query(default=None),
    ingredients: str | None = Query(default=None),
    exclude_ingredients: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Render the search page with filters and results.

    A search is only performed when at least one filter is active
    (q, tags, language, ingredients, or exclude_ingredients).
    If no filter is active, the page shows an empty state with a hint.
    """
    # Determine if the user actually submitted a search
    query_made = bool(q or tags or language or ingredients or exclude_ingredients)

    recipes = []
    has_next = False

    if query_made:
        # Parse comma-separated ingredient lists
        ingredient_list = [i.strip().lower() for i in ingredients.split(",")] \
            if ingredients else []
        exclude_list = [i.strip().lower() for i in exclude_ingredients.split(",")] \
            if exclude_ingredients else []

        # Base query — published recipes only
        query = (
            select(Recipe)
            .where(Recipe.status == RecipeStatus.PUBLISHED)
            .options(
                selectinload(Recipe.author),
                selectinload(Recipe.translations),
                selectinload(Recipe.photos),
            )
            .order_by(Recipe.published_at.desc())
        )

        # Language filter
        if language:
            query = query.where(Recipe.original_language == language)

        # Tag filters (AND logic — all selected tags must be present)
        for tag in tags:
            query = query.where(
                or_(
                    Recipe.dietary_tags.contains([tag]),
                    Recipe.metabolic_tags.contains([tag]),
                )
            )

        # Full-text search on title + description
        if q:
            tsquery = func.plainto_tsquery("simple", q)
            fts_condition = exists(
                select(RecipeTranslation.id).where(
                    RecipeTranslation.recipe_id == Recipe.id,
                    func.to_tsvector(
                        "simple",
                        func.coalesce(RecipeTranslation.title, "")
                        + " "
                        + func.coalesce(RecipeTranslation.description, ""),
                    ).op("@@")(tsquery),
                )
            )
            query = query.where(fts_condition)

        # Include ingredient filter — must have at least one match
        if ingredient_list:
            query = query.where(
                or_(*[
                    exists(
                        select(RecipeIngredient.id).where(
                            RecipeIngredient.recipe_id == Recipe.id,
                            func.lower(RecipeIngredient.name).contains(ing),
                        )
                    )
                    for ing in ingredient_list
                ])
            )

        # Exclude ingredient filter — must have none of these
        for ing in exclude_list:
            query = query.where(
                not_(
                    exists(
                        select(RecipeIngredient.id).where(
                            RecipeIngredient.recipe_id == Recipe.id,
                            func.lower(RecipeIngredient.name).contains(ing),
                        )
                    )
                )
            )

        # Pagination — fetch one extra to detect next page
        query = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1)

        result = await db.execute(query)
        rows = result.scalars().all()

        has_next = len(rows) > PAGE_SIZE
        rows = rows[:PAGE_SIZE]

        # Build flat dicts for the template
        for recipe in rows:
            translation = next(
                (t for t in recipe.translations if t.language == recipe.original_language),
                recipe.translations[0] if recipe.translations else None,
            )
            cover = next((p for p in recipe.photos if p.is_cover), None)
            if cover is None and recipe.photos:
                cover = recipe.photos[0]

            recipes.append({
                "id": str(recipe.id),
                "title": translation.title if translation else recipe.slug,
                "author_username": recipe.author.username,
                "author_display_name": recipe.author.display_name,
                "dietary_tags": recipe.dietary_tags or [],
                "prep_time_seconds": recipe.prep_time_seconds,
                "cook_time_seconds": recipe.cook_time_seconds,
                "servings": recipe.servings,
                "cover_url": f"/media/{cover.url}" if cover else None,
                "cover_alt": cover.alt_text if cover else None,
            })

    # Build a helper to generate pagination URLs preserving current filters.
    # We pass it as a Jinja2 callable so the template can call page_url(n).
    def page_url(p: int) -> str:
        return _page_url(request, p)

    return templates.TemplateResponse("search.html", {
        "request": request,
        "current_user": current_user,
        # Search state
        "q": q or "",
        "selected_tags": tags,
        "language": language or "",
        "ingredients": ingredients or "",
        "exclude_ingredients": exclude_ingredients or "",
        "query_made": query_made,
        # Results
        "recipes": recipes,
        "page": page,
        "has_next": has_next,
        "page_url": page_url,
    })
