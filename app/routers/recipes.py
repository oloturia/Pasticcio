# ============================================================
# app/routers/recipes.py — recipe endpoints
# ============================================================
#
# Endpoints:
#   POST   /api/v1/recipes/      — create a recipe
#   GET    /api/v1/recipes/      — list recipes (with filters)
#   GET    /api/v1/recipes/{id}  — get a single recipe
#   PUT    /api/v1/recipes/{id}  — update a recipe
#   DELETE /api/v1/recipes/{id}  — delete a recipe
#
# Federation delivery:
#   When a recipe is published (new or via update) or updated while
#   already published, we enqueue a Celery task that delivers the
#   activity to all followers in the background.
#   The API response is returned immediately — delivery is async.

import uuid
import httpx
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, field_validator
from slugify import slugify
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.recipe import (
    Difficulty,
    IngredientUnit,
    Recipe,
    RecipeIngredient,
    RecipePhoto,
    RecipeStatus,
    RecipeTranslation,
    TranslationStatus,
)
from app.models.user import User
from app.models.reaction import Reaction, ReactionType
from app.models.moderation import Bookmark
from app.models.cooked_this import CookedThis, CookedThisStatus
from app.routers.auth import get_current_user
from app.tasks.delivery import deliver_to_followers
from app.templates_env import templates
from app.dependencies import get_current_user_optional
from app.routers.recipe_utils import unit_options_html

router = APIRouter(prefix="/api/v1/recipes", tags=["recipes"])


# ============================================================
# Pydantic schemas
# ============================================================

class StepSchema(BaseModel):
    order: int
    text: str


class IngredientIn(BaseModel):
    sort_order: int = 0
    quantity: float | None = None
    unit: IngredientUnit = IngredientUnit.NONE
    name: str
    notes: str | None = None


class TranslationIn(BaseModel):
    language: str
    title: str
    description: str | None = None
    steps: list[StepSchema] = []
    categories: list[str] = []


class RecipeCreateRequest(BaseModel):
    translation: TranslationIn
    original_language: str = "en"
    ingredients: list[IngredientIn] = []
    dietary_tags: list[str] = []
    metabolic_tags: list[str] = []
    prep_time_seconds: int | None = None
    cook_time_seconds: int | None = None
    servings: int | None = None
    difficulty: Difficulty | None = None
    publish: bool = False

    @field_validator("dietary_tags", "metabolic_tags")
    @classmethod
    def tags_lowercase(cls, v: list[str]) -> list[str]:
        return [tag.lower().strip() for tag in v]


class RecipeUpdateRequest(BaseModel):
    translation: TranslationIn | None = None
    ingredients: list[IngredientIn] | None = None
    dietary_tags: list[str] | None = None
    metabolic_tags: list[str] | None = None
    prep_time_seconds: int | None = None
    cook_time_seconds: int | None = None
    servings: int | None = None
    difficulty: Difficulty | None = None
    publish: bool | None = None


class IngredientOut(BaseModel):
    id: uuid.UUID
    sort_order: int
    quantity: float | None
    unit: str
    name: str
    notes: str | None

    model_config = {"from_attributes": True}


class TranslationOut(BaseModel):
    id: uuid.UUID
    language: str
    title: str
    description: str | None
    steps: list[dict]
    categories: list[str]
    status: str

    model_config = {"from_attributes": True}


class AuthorOut(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str | None
    ap_id: str

    model_config = {"from_attributes": True}


class RecipeOut(BaseModel):
    id: uuid.UUID
    slug: str
    ap_id: str
    status: str
    original_language: str
    dietary_tags: list[str]
    metabolic_tags: list[str]
    show_metabolic_disclaimer: bool
    prep_time_seconds: int | None
    cook_time_seconds: int | None
    servings: int | None
    difficulty: str | None
    created_at: datetime
    published_at: datetime | None
    author: AuthorOut
    translations: list[TranslationOut]
    ingredients: list[IngredientOut]
    likes_count: int = 0
    announces_count: int = 0
    forked_from: str | None = None

    model_config = {"from_attributes": True}


class RecipeListItem(BaseModel):
    """Lighter version of RecipeOut for list views — no ingredients."""
    id: uuid.UUID
    slug: str
    ap_id: str
    status: str
    original_language: str
    dietary_tags: list[str]
    metabolic_tags: list[str]
    prep_time_seconds: int | None
    cook_time_seconds: int | None
    servings: int | None
    difficulty: str | None
    published_at: datetime | None
    author: AuthorOut
    translations: list[TranslationOut]

    model_config = {"from_attributes": True}

class ForkRequest(BaseModel):
    """Request body for forking a remote recipe."""
    ap_id: str  # AP ID of the remote recipe to fork
    
# ============================================================
# Helpers
# ============================================================

def _build_ap_id(username: str, slug: str) -> str:
    return f"https://{settings.instance_domain}/users/{username}/recipes/{slug}"


def _trigger_delivery(recipe_id: uuid.UUID, activity_type: str) -> None:
    """
    Enqueue a Celery delivery task if the worker is available.

    We wrap the import in a try/except so that tests (which do not
    run a real Celery worker) do not fail just because the broker
    is unreachable. In production the broker is always up.
    """
    try:
        deliver_to_followers.delay(str(recipe_id), activity_type)
    except Exception:
        # Log but do not raise — delivery failure must never break the API
        import logging
        logging.getLogger(__name__).warning(
            "Could not enqueue delivery task for recipe %s", recipe_id
        )


# ============================================================
# Endpoints
# ============================================================

@router.post("/", response_model=RecipeOut, status_code=status.HTTP_201_CREATED)
async def create_recipe(
    data: RecipeCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new recipe. Requires authentication."""

    base_slug = slugify(data.translation.title)
    slug = base_slug

    existing = await db.execute(
        select(Recipe).where(
            and_(Recipe.author_id == current_user.id, Recipe.slug == slug)
        )
    )
    if existing.scalar_one_or_none():
        slug = f"{base_slug}-{str(uuid.uuid4())[:8]}"

    recipe_id = uuid.uuid4()
    ap_id = _build_ap_id(current_user.username, slug)
    has_metabolic = len(data.metabolic_tags) > 0
    is_published = data.publish

    recipe = Recipe(
        id=recipe_id,
        author_id=current_user.id,
        slug=slug,
        ap_id=ap_id,
        original_language=data.original_language,
        status=RecipeStatus.PUBLISHED if is_published else RecipeStatus.DRAFT,
        published_at=datetime.now(timezone.utc) if is_published else None,
        dietary_tags=data.dietary_tags,
        metabolic_tags=data.metabolic_tags,
        show_metabolic_disclaimer=has_metabolic,
        prep_time_seconds=data.prep_time_seconds,
        cook_time_seconds=data.cook_time_seconds,
        servings=data.servings,
        difficulty=data.difficulty,
    )
    db.add(recipe)
    await db.flush()

    translation = RecipeTranslation(
        recipe_id=recipe_id,
        language=data.translation.language,
        title=data.translation.title,
        description=data.translation.description,
        steps=[s.model_dump() for s in data.translation.steps],
        categories=data.translation.categories,
        status=TranslationStatus.ORIGINAL,
        translated_by_id=None,
    )
    db.add(translation)

    for ing_data in data.ingredients:
        db.add(RecipeIngredient(
            recipe_id=recipe_id,
            sort_order=ing_data.sort_order,
            quantity=ing_data.quantity,
            unit=ing_data.unit,
            name=ing_data.name,
            notes=ing_data.notes,
        ))

    await db.flush()

    # Deliver to followers if published immediately
    if is_published:
        _trigger_delivery(recipe_id, "create")

    result = await db.execute(
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(
            selectinload(Recipe.author),
            selectinload(Recipe.translations),
            selectinload(Recipe.ingredients),
        )
    )
    return result.scalar_one()


@router.get("/", response_model=list[RecipeListItem])
async def list_recipes(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    vegan: bool = Query(default=False),
    vegetarian: bool = Query(default=False),
    gluten_free: bool = Query(default=False),
    language: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    List published recipes with optional filters.
    Filters are inclusive — selecting vegan shows only vegan recipes.
    """
    query = (
        select(Recipe)
        .where(Recipe.status == RecipeStatus.PUBLISHED)
        .options(
            selectinload(Recipe.author),
            selectinload(Recipe.translations),
        )
        .order_by(Recipe.published_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )

    if vegan:
        query = query.where(Recipe.dietary_tags.contains(["vegan"]))
    if vegetarian:
        query = query.where(Recipe.dietary_tags.contains(["vegetarian"]))
    if gluten_free:
        query = query.where(Recipe.dietary_tags.contains(["gluten_free"]))
    if language:
        query = query.where(Recipe.original_language == language)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{recipe_id}", response_model=RecipeOut)
async def get_recipe(
    recipe_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Get a single recipe by ID. Returns HTML for browsers, JSON for AP clients."""
    result = await db.execute(
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(
            selectinload(Recipe.author),
            selectinload(Recipe.translations),
            selectinload(Recipe.ingredients),
            selectinload(Recipe.photos),
            selectinload(Recipe.step_photos),
            selectinload(Recipe.cooked_this).options(
                selectinload(CookedThis.author),
                selectinload(CookedThis.photos),
                selectinload(CookedThis.replies).selectinload(CookedThis.author),
            ),
        )
    )
    recipe = result.scalar_one_or_none()
    if not recipe or recipe.status == RecipeStatus.DELETED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")

    step_photo_map = {
        p.step_order: f"/media/{p.url}"
        for p in recipe.step_photos
    }

    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/activity+json" not in accept:
        # Pick the best available translation
        translation = next(
            (t for t in recipe.translations if t.language == recipe.original_language),
            recipe.translations[0] if recipe.translations else None,
        )
        steps = sorted(translation.steps, key=lambda s: s.get("order", 0)) if translation else []
        comments = [
            c for c in recipe.cooked_this
            if c.status == CookedThisStatus.PUBLISHED and c.parent_id is None
        ]
        comments.sort(key=lambda c: c.created_at)
        cover = next((p for p in recipe.photos if p.is_cover), None)
        if cover is None and recipe.photos:
            cover = recipe.photos[0]

        # Check if current user has bookmarked this recipe
        is_bookmarked = False
        bookmark_id = None
        if current_user:
            bm_result = await db.execute(
                select(Bookmark).where(
                    Bookmark.user_id == current_user.id,
                    Bookmark.recipe_ap_id == recipe.ap_id,
                )
            )
            bm = bm_result.scalar_one_or_none()
            if bm:
                is_bookmarked = True
                bookmark_id = str(bm.id)

        return templates.TemplateResponse(
            "recipe_detail.html",
            {
                "request": request,
                "recipe": recipe,
                "translation": translation,
                "ingredients": recipe.ingredients,
                "steps": steps,
                "cover_url": f"/media/{cover.url}" if cover else None,
                "cover_alt": cover.alt_text if cover else None,
                "step_photo_map": step_photo_map,
                "current_user": current_user,
                "comments": comments,
                "comment_error": None,
                "comment_content": None,
                "unit_options_html": unit_options_html(),
                "is_bookmarked": is_bookmarked,
                "bookmark_id": bookmark_id,
            },
        )

    # Default: JSON for API and AP clients
    likes_result = await db.execute(
        select(func.count()).where(
            Reaction.recipe_id == recipe_id,
            Reaction.reaction_type == ReactionType.LIKE,
        )
    )
    announces_result = await db.execute(
        select(func.count()).where(
            Reaction.recipe_id == recipe_id,
            Reaction.reaction_type == ReactionType.ANNOUNCE,
        )
    )
    data = RecipeOut.model_validate(recipe)
    data.likes_count = likes_result.scalar() or 0
    data.announces_count = announces_result.scalar() or 0
    return data
@router.put("/{recipe_id}", response_model=RecipeOut)
async def update_recipe(
    recipe_id: uuid.UUID,
    data: RecipeUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a recipe. Only the author can update their own recipes."""
    result = await db.execute(
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(
            selectinload(Recipe.author),
            selectinload(Recipe.translations),
            selectinload(Recipe.ingredients),
        )
    )
    recipe = result.scalar_one_or_none()

    if not recipe or recipe.status == RecipeStatus.DELETED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")
    if recipe.author_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your recipe")

    was_published = recipe.status == RecipeStatus.PUBLISHED

    if data.dietary_tags is not None:
        recipe.dietary_tags = data.dietary_tags
    if data.metabolic_tags is not None:
        recipe.metabolic_tags = data.metabolic_tags
        recipe.show_metabolic_disclaimer = len(data.metabolic_tags) > 0
    if data.prep_time_seconds is not None:
        recipe.prep_time_seconds = data.prep_time_seconds
    if data.cook_time_seconds is not None:
        recipe.cook_time_seconds = data.cook_time_seconds
    if data.servings is not None:
        recipe.servings = data.servings
    if data.difficulty is not None:
        recipe.difficulty = data.difficulty

    just_published = False
    if data.publish is True and recipe.status == RecipeStatus.DRAFT:
        recipe.status = RecipeStatus.PUBLISHED
        recipe.published_at = datetime.now(timezone.utc)
        just_published = True

    if data.translation:
        trans_result = await db.execute(
            select(RecipeTranslation).where(
                and_(
                    RecipeTranslation.recipe_id == recipe_id,
                    RecipeTranslation.language == data.translation.language,
                )
            )
        )
        translation = trans_result.scalar_one_or_none()
        if translation:
            translation.title = data.translation.title
            translation.description = data.translation.description
            translation.steps = [s.model_dump() for s in data.translation.steps]
            translation.categories = data.translation.categories
        else:
            db.add(RecipeTranslation(
                recipe_id=recipe_id,
                language=data.translation.language,
                title=data.translation.title,
                description=data.translation.description,
                steps=[s.model_dump() for s in data.translation.steps],
                categories=data.translation.categories,
                status=TranslationStatus.DRAFT,
                translated_by_id=current_user.id,
            ))

    if data.ingredients is not None:
        for ing in recipe.ingredients:
            await db.delete(ing)
        await db.flush()
        for ing_data in data.ingredients:
            db.add(RecipeIngredient(
                recipe_id=recipe_id,
                sort_order=ing_data.sort_order,
                quantity=ing_data.quantity,
                unit=ing_data.unit,
                name=ing_data.name,
                notes=ing_data.notes,
            ))

    await db.flush()

    # Trigger delivery:
    # - just_published → Create activity (first time followers see this)
    # - was_published and not just_published → Update activity
    if just_published:
        _trigger_delivery(recipe_id, "create")
    elif was_published:
        _trigger_delivery(recipe_id, "update")

    result = await db.execute(
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(
            selectinload(Recipe.author),
            selectinload(Recipe.translations),
            selectinload(Recipe.ingredients),
        )
    )
    return result.scalar_one()


@router.delete("/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recipe(
    recipe_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Soft-delete a recipe. Sets status to DELETED instead of removing
    the row, so federation tombstones can still be sent.
    """
    result = await db.execute(select(Recipe).where(Recipe.id == recipe_id))
    recipe = result.scalar_one_or_none()

    if not recipe or recipe.status == RecipeStatus.DELETED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")
    if recipe.author_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your recipe")

    was_published = recipe.status == RecipeStatus.PUBLISHED
    recipe.status = RecipeStatus.DELETED

    # Deliver Delete{Tombstone} to followers
    if was_published:
        _trigger_delivery(recipe_id, "delete")

@router.post("/fork", response_model=RecipeOut, status_code=status.HTTP_201_CREATED)
async def fork_recipe(
    data: ForkRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Fork a remote recipe.

    Fetches the recipe from the remote server using its AP ID,
    creates a local copy owned by the authenticated user, and
    records the original AP ID in the forked_from field.

    The forked recipe is saved as a draft — the user can review
    and publish it when ready.
    """

    # Fetch the remote recipe
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                data.ap_id,
                headers={"Accept": "application/activity+json"},
                follow_redirects=True,
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=422,
                    detail=f"Could not fetch remote recipe: HTTP {resp.status_code}",
                )
            remote = resp.json()
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not fetch remote recipe: {exc}",
        )

    # Validate that this looks like a Pasticcio recipe Article
    if remote.get("type") != "Article":
        raise HTTPException(
            status_code=422,
            detail="The provided AP ID does not point to a recipe (Article)",
        )

    # Extract title from name or pasticcio:title
    title = (
        remote.get("name")
        or remote.get("pasticcio:title")
        or "Forked recipe"
    )

    # Extract description
    description = remote.get("summary") or remote.get("content") or None

    # Extract dietary tags
    dietary_tags = []
    for tag in remote.get("tag", []):
        if isinstance(tag, dict) and tag.get("type") == "Hashtag":
            name = tag.get("name", "").lstrip("#").lower()
            if name and name not in ("cookedthis",):
                dietary_tags.append(name)

    # Extract pasticcio-specific fields if present
    servings = remote.get("pasticcio:servings")
    prep_time = remote.get("pasticcio:prepTime")
    cook_time = remote.get("pasticcio:cookTime")

    # Convert ISO 8601 durations to seconds if present
    prep_seconds = None
    cook_seconds = None
    if prep_time:
        try:
            import isodate
            prep_seconds = int(isodate.parse_duration(prep_time).total_seconds())
        except Exception:
            pass
    if cook_time:
        try:
            import isodate
            cook_seconds = int(isodate.parse_duration(cook_time).total_seconds())
        except Exception:
            pass

    # Generate slug from title
    base_slug = slugify(title)
    slug = base_slug
    existing = await db.execute(
        select(Recipe).where(
            and_(Recipe.author_id == current_user.id, Recipe.slug == slug)
        )
    )
    if existing.scalar_one_or_none():
        import uuid as _uuid
        slug = f"{base_slug}-{str(_uuid.uuid4())[:8]}"

    recipe_id = uuid.uuid4()
    ap_id = _build_ap_id(current_user.username, slug)

    recipe = Recipe(
        id=recipe_id,
        author_id=current_user.id,
        slug=slug,
        ap_id=ap_id,
        original_language=remote.get("inLanguage", current_user.preferred_language or "en"),
        status=RecipeStatus.DRAFT,
        published_at=None,
        dietary_tags=dietary_tags,
        metabolic_tags=[],
        show_metabolic_disclaimer=False,
        prep_time_seconds=prep_seconds,
        cook_time_seconds=cook_seconds,
        servings=int(servings) if servings else None,
        forked_from=data.ap_id,
    )
    db.add(recipe)
    await db.flush()

    # Add translation from remote content
    translation = RecipeTranslation(
        recipe_id=recipe_id,
        language=remote.get("inLanguage", current_user.preferred_language or "en"),
        title=title,
        description=description,
        steps=[],  # Steps are not easily extractable from generic AP content
        status=TranslationStatus.ORIGINAL,
        translated_by_id=None,
    )
    db.add(translation)
    await db.flush()

    # Reload for response
    result = await db.execute(
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(
            selectinload(Recipe.author),
            selectinload(Recipe.translations),
            selectinload(Recipe.ingredients),
        )
    )
    return result.scalar_one()
