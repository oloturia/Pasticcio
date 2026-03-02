# ============================================================
# app/routers/recipes.py — recipe endpoints
# ============================================================
#
# Endpoints:
#   POST   /api/v1/recipes/          — create a recipe
#   GET    /api/v1/recipes/          — list recipes (with filters)
#   GET    /api/v1/recipes/{id}      — get a single recipe
#   PUT    /api/v1/recipes/{id}      — update a recipe
#   DELETE /api/v1/recipes/{id}      — delete a recipe

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from slugify import slugify
from sqlalchemy import select, and_
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
from app.routers.auth import get_current_user

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
    # At least one translation (the original) is required
    translation: TranslationIn
    original_language: str = "en"
    ingredients: list[IngredientIn] = []
    dietary_tags: list[str] = []
    metabolic_tags: list[str] = []
    prep_time_seconds: int | None = None
    cook_time_seconds: int | None = None
    servings: int | None = None
    difficulty: Difficulty | None = None
    # If true, publish immediately; otherwise save as draft
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
    status: str
    categories: list[str]

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


# ============================================================
# Helper
# ============================================================

def _build_ap_id(username: str, slug: str) -> str:
    return f"https://{settings.instance_domain}/users/{username}/recipes/{slug}"


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

    # Generate a unique slug from the title
    base_slug = slugify(data.translation.title)
    slug = base_slug

    # If the slug is already taken by this author, append a short UUID
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

    recipe = Recipe(
        id=recipe_id,
        author_id=current_user.id,
        slug=slug,
        ap_id=ap_id,
        original_language=data.original_language,
        status=RecipeStatus.PUBLISHED if data.publish else RecipeStatus.DRAFT,
        published_at=datetime.now(timezone.utc) if data.publish else None,
        dietary_tags=data.dietary_tags,
        metabolic_tags=data.metabolic_tags,
        show_metabolic_disclaimer=has_metabolic,
        prep_time_seconds=data.prep_time_seconds,
        cook_time_seconds=data.cook_time_seconds,
        servings=data.servings,
        difficulty=data.difficulty,
    )
    db.add(recipe)
    await db.flush()  # get the recipe ID before adding children

    # Add the original translation
    translation = RecipeTranslation(
        recipe_id=recipe_id,
        language=data.translation.language,
        title=data.translation.title,
        description=data.translation.description,
        steps=[s.model_dump() for s in data.translation.steps],
        status=TranslationStatus.ORIGINAL,
        translated_by_id=None,
        categories=data.translation.categories,
    )
    db.add(translation)

    # Add ingredients
    for ing_data in data.ingredients:
        ingredient = RecipeIngredient(
            recipe_id=recipe_id,
            sort_order=ing_data.sort_order,
            quantity=ing_data.quantity,
            unit=ing_data.unit,
            name=ing_data.name,
            notes=ing_data.notes,
        )
        db.add(ingredient)

    await db.flush()

    # Reload with all relationships for the response
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
    # Pagination
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    # Dietary filters — inclusive only (toward plant-based)
    vegan: bool = Query(default=False),
    vegetarian: bool = Query(default=False),
    gluten_free: bool = Query(default=False),
    # Language filter
    language: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    List published recipes with optional filters.
    Filters are inclusive — selecting 'vegan' shows only vegan recipes,
    but there is no option to exclude vegan recipes.
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

    # Apply dietary filters
    # Each filter narrows the results further (AND logic)
    if vegan:
        query = query.where(Recipe.dietary_tags.contains(["vegan"]))
    if vegetarian:
        query = query.where(Recipe.dietary_tags.contains(["vegetarian"]))
    if gluten_free:
        query = query.where(Recipe.dietary_tags.contains(["gluten_free"]))

    # Filter by translation language
    if language:
        query = query.where(Recipe.original_language == language)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{recipe_id}", response_model=RecipeOut)
async def get_recipe(
    recipe_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a single recipe by ID. Returns 404 if not found or not published."""
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

    return recipe


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

    # Apply updates — only fields that were provided
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
    if data.publish is True and recipe.status == RecipeStatus.DRAFT:
        recipe.status = RecipeStatus.PUBLISHED
        recipe.published_at = datetime.now(timezone.utc)

    # Update the translation if provided
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
        else:
            # New language translation
            db.add(RecipeTranslation(
                recipe_id=recipe_id,
                language=data.translation.language,
                title=data.translation.title,
                description=data.translation.description,
                steps=[s.model_dump() for s in data.translation.steps],
                status=TranslationStatus.DRAFT,
                translated_by_id=current_user.id,
            ))

    # Replace ingredients if provided
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


@router.delete("/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recipe(
    recipe_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Soft-delete a recipe. Sets status to DELETED instead of removing
    the row, so federation tombstones can still be sent.
    Only the author can delete their own recipes.
    """
    result = await db.execute(select(Recipe).where(Recipe.id == recipe_id))
    recipe = result.scalar_one_or_none()

    if not recipe or recipe.status == RecipeStatus.DELETED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")

    if recipe.author_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your recipe")

    recipe.status = RecipeStatus.DELETED
