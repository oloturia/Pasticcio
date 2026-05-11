# ============================================================
# app/routers/search.py — recipe search endpoint
# ============================================================
#
# Endpoints:
#   GET /api/v1/search — search recipes locally
#
# Query parameters:
#   q                   — full-text search on title and description
#   tags                — comma-separated dietary/metabolic/category tags
#   ingredients         — comma-separated ingredients to include
#   exclude_ingredients — comma-separated ingredients to exclude
#   language            — filter by original language (BCP-47)
#   page                — pagination (default 1)
#   per_page            — results per page (default 20, max 50)
#
# Ingredient search logic:
#   include: recipes that have AT LEAST ONE of the listed ingredients,
#            ranked by how many they have (most matches first)
#   exclude: recipes that have NONE of the listed ingredients

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_, or_, not_, exists, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.recipe import (
    Recipe,
    RecipeIngredient,
    RecipeStatus,
    RecipeTranslation,
)

from app.ap.instances import get_pasticcio_instances
from app.models.known_instance import KnownInstance

router = APIRouter(prefix="/api/v1/search", tags=["search"])


# ============================================================
# Pydantic schemas
# ============================================================

class TranslationSummary(BaseModel):
    language: str
    title: str
    description: str | None

    model_config = {"from_attributes": True}


class AuthorSummary(BaseModel):
    username: str
    display_name: str | None
    ap_id: str

    model_config = {"from_attributes": True}


class SearchResult(BaseModel):
    id: uuid.UUID
    slug: str
    ap_id: str
    original_language: str
    dietary_tags: list[str]
    metabolic_tags: list[str]
    servings: int | None
    published_at: datetime | None
    forked_from: str | None
    author: AuthorSummary
    translations: list[TranslationSummary]
    # How many of the requested ingredients were matched (for ranking)
    ingredient_match_count: int = 0

    model_config = {"from_attributes": True}


# ============================================================
# Endpoint
# ============================================================

@router.get("/", response_model=list[SearchResult])
async def search_recipes(
    # Full-text query
    q: str | None = Query(default=None, description="Search in title and description"),
    # Tag filters (inclusive AND)
    tags: str | None = Query(default=None, description="Comma-separated tags (e.g. vegan,gluten_free)"),
    # Ingredient filters
    ingredients: str | None = Query(
        default=None,
        description="Comma-separated ingredients to include (ranked by match count)",
    ),
    exclude_ingredients: str | None = Query(
        default=None,
        description="Comma-separated ingredients to exclude",
    ),
    # Language filter
    language: str | None = Query(default=None, description="Filter by original language (e.g. en, it)"),
    # Pagination
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """
    Search published recipes.

    Results are ranked by relevance:
    - Full-text match score (if q is provided)
    - Ingredient match count (if ingredients are provided)
    - Most recently published (as tiebreaker)
    """
    # Parse comma-separated parameters
    tag_list = [t.strip().lower() for t in tags.split(",")] if tags else []
    ingredient_list = [i.strip().lower() for i in ingredients.split(",")] if ingredients else []
    exclude_list = [i.strip().lower() for i in exclude_ingredients.split(",")] if exclude_ingredients else []

    # Base query — only published recipes
    query = (
        select(Recipe)
        .where(Recipe.status == RecipeStatus.PUBLISHED)
        .options(
            selectinload(Recipe.author),
            selectinload(Recipe.translations),
        )
    )

    # Language filter
    if language:
        query = query.where(Recipe.original_language == language)

    # Tag filters — each tag must be present (AND logic)
    for tag in tag_list:
        query = query.where(
            or_(
                Recipe.dietary_tags.contains([tag]),
                Recipe.metabolic_tags.contains([tag]),
                Recipe.categories.contains([tag]),
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

    # Ingredient include filter — must have at least one
    if ingredient_list:
        ingredient_conditions = [
            exists(
                select(RecipeIngredient.id).where(
                    RecipeIngredient.recipe_id == Recipe.id,
                    func.lower(RecipeIngredient.name).contains(ing),
                )
            )
            for ing in ingredient_list
        ]
        query = query.where(or_(*ingredient_conditions))

    # Ingredient exclude filter — must have none of these
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

    # Order by most recently published
    query = query.order_by(Recipe.published_at.desc())

    # Pagination
    query = query.offset((page - 1) * per_page).limit(per_page)

    result = await db.execute(query)
    recipes = result.scalars().all()

    # Build results with ingredient match count for ranking
    output = []
    for recipe in recipes:
        match_count = 0
        if ingredient_list:
            # Count how many of the requested ingredients this recipe has
            ing_result = await db.execute(
                select(func.count(RecipeIngredient.id)).where(
                    RecipeIngredient.recipe_id == recipe.id,
                    or_(
                        *[
                            func.lower(RecipeIngredient.name).contains(ing)
                            for ing in ingredient_list
                        ]
                    ),
                )
            )
            match_count = ing_result.scalar() or 0

        translations_summary = [
            TranslationSummary(
                language=t.language,
                title=t.title,
                description=t.description,
            )
            for t in recipe.translations
        ]

        output.append(
            SearchResult(
                id=recipe.id,
                slug=recipe.slug,
                ap_id=recipe.ap_id,
                original_language=recipe.original_language,
                dietary_tags=recipe.dietary_tags,
                metabolic_tags=recipe.metabolic_tags,
                servings=recipe.servings,
                published_at=recipe.published_at,
                forked_from=recipe.forked_from,
                author=AuthorSummary(
                    username=recipe.author.username,
                    display_name=recipe.author.display_name,
                    ap_id=recipe.author.ap_id,
                ),
                translations=translations_summary,
                ingredient_match_count=match_count,
            )
        )

    # Re-sort by ingredient match count if ingredients were specified
    if ingredient_list:
        output.sort(key=lambda r: r.ingredient_match_count, reverse=True)

    return output
    
@router.get("/federated", response_model=list[SearchResult])
async def federated_search(
    q: str | None = Query(default=None),
    tags: str | None = Query(default=None),
    ingredients: str | None = Query(default=None),
    exclude_ingredients: str | None = Query(default=None),
    language: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """
    Search recipes across all known Pasticcio instances.

    Queries this instance plus all known Pasticcio instances.
    Results are aggregated and sorted by published_at.
    Remote results cannot be paginated the same way as local ones.
    """
    # Get local results first
    local_results = await search_recipes(
        q=q, tags=tags, ingredients=ingredients,
        exclude_ingredients=exclude_ingredients,
        language=language, page=page, per_page=per_page, db=db,
    )

    # Get known Pasticcio instances
    instances = await get_pasticcio_instances(db)
    if not instances:
        return local_results

    # Query each remote instance
    remote_results = []
    params = {}
    if q:
        params["q"] = q
    if tags:
        params["tags"] = tags
    if ingredients:
        params["ingredients"] = ingredients
    if exclude_ingredients:
        params["exclude_ingredients"] = exclude_ingredients
    if language:
        params["language"] = language
    params["per_page"] = per_page

    async with httpx.AsyncClient(timeout=5.0) as client:
        for domain in instances:
            try:
                resp = await client.get(
                    f"https://{domain}/api/v1/search/",
                    params=params,
                )
                if resp.status_code == 200:
                    for item in resp.json():
                        # Convert remote result to SearchResult
                        # Remote results won\'t have full author objects
                        # so we use minimal data
                        try:
                            remote_results.append(SearchResult(
                                id=item["id"],
                                slug=item["slug"],
                                ap_id=item["ap_id"],
                                original_language=item.get("original_language", "en"),
                                dietary_tags=item.get("dietary_tags", []),
                                metabolic_tags=item.get("metabolic_tags", []),
                                servings=item.get("servings"),
                                published_at=item.get("published_at"),
                                forked_from=item.get("forked_from"),
                                author=AuthorSummary(
                                    username=item["author"]["username"],
                                    display_name=item["author"].get("display_name"),
                                    ap_id=item["author"]["ap_id"],
                                ),
                                translations=[
                                    TranslationSummary(
                                        language=t["language"],
                                        title=t["title"],
                                        description=t.get("description"),
                                    )
                                    for t in item.get("translations", [])
                                ],
                                ingredient_match_count=item.get("ingredient_match_count", 0),
                            ))
                        except Exception:
                            pass
            except httpx.RequestError:
                pass  # Remote instance unreachable — skip silently

    # Combine and sort by published_at descending
    all_results = local_results + remote_results
    all_results.sort(
        key=lambda r: r.published_at or "",
        reverse=True,
    )

    # Deduplicate by ap_id
    seen = set()
    deduped = []
    for r in all_results:
        if r.ap_id not in seen:
            seen.add(r.ap_id)
            deduped.append(r)

    return deduped[:per_page]
