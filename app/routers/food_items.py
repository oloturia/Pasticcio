# ============================================================
# app/routers/food_items.py — food item search and ingredient linking
# ============================================================
#
# Endpoints:
#   GET  /api/v1/food-items/search            — full-text search in food_items
#   POST /api/v1/recipes/{id}/ingredients/{ing_id}/link — link ingredient to food_item
#   DELETE /api/v1/recipes/{id}/ingredients/{ing_id}/link — unlink

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.recipe import Recipe, RecipeIngredient, RecipeStatus
from app.models.recipe import FoodItem
from app.dependencies import get_current_user_optional
from app.models.user import User

router = APIRouter(tags=["nutrition"])


# ============================================================
# Schemas
# ============================================================

class FoodItemOut(BaseModel):
    id: uuid.UUID
    name: str
    source: str | None
    kcal: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carbs_g: float | None = None
    fiber_g: float | None = None

    model_config = {"from_attributes": True}


class LinkRequest(BaseModel):
    food_item_id: uuid.UUID
    quantity_grams: float | None = None  # manual override for unit conversion


# ============================================================
# Helpers
# ============================================================

def _food_item_out(fi: FoodItem) -> FoodItemOut:
    p = fi.per_100g or {}
    return FoodItemOut(
        id=fi.id,
        name=fi.name,
        source=fi.source,
        kcal=p.get("kcal"),
        protein_g=p.get("protein_g"),
        fat_g=p.get("fat_g"),
        carbs_g=p.get("carbs_g"),
        fiber_g=p.get("fiber_g"),
    )


async def _get_ingredient_for_owner(
    recipe_id: uuid.UUID,
    ingredient_id: uuid.UUID,
    current_user: User | None,
    db: AsyncSession,
) -> RecipeIngredient:
    """Load a recipe ingredient and verify the current user is the recipe author."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    recipe_result = await db.execute(
        select(Recipe).where(
            Recipe.id == recipe_id,
            Recipe.status != RecipeStatus.DELETED,
        )
    )
    recipe = recipe_result.scalar_one_or_none()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    if recipe.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your recipe")

    ing_result = await db.execute(
        select(RecipeIngredient).where(
            RecipeIngredient.id == ingredient_id,
            RecipeIngredient.recipe_id == recipe_id,
        )
    )
    ing = ing_result.scalar_one_or_none()
    if not ing:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    return ing


# ============================================================
# Endpoints
# ============================================================

@router.get("/api/v1/food-items/search", response_model=list[FoodItemOut])
async def search_food_items(
    q: str = Query(..., min_length=2, description="Search term"),
    limit: int = Query(default=15, le=50),
    db: AsyncSession = Depends(get_db),
):
    """
    Search food items by name.
    Returns up to `limit` results sorted by relevance.
    Uses PostgreSQL full-text search with a LIKE fallback.
    """
    q_clean = q.strip()

    result = await db.execute(
        select(FoodItem)
        .where(
            func.lower(FoodItem.name).contains(q_clean.lower())
        )
        .order_by(
            # Exact prefix match first, then contains
            func.lower(FoodItem.name).startswith(q_clean.lower()).desc(),
            FoodItem.name,
        )
        .limit(limit)
    )
    items = result.scalars().all()
    return [_food_item_out(fi) for fi in items]


@router.post(
    "/api/v1/recipes/{recipe_id}/ingredients/{ingredient_id}/link",
)
async def link_ingredient(
    recipe_id: uuid.UUID,
    ingredient_id: uuid.UUID,
    data: LinkRequest,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Link a recipe ingredient to a FoodItem.
    Only the recipe author can do this.
    """
    ing = await _get_ingredient_for_owner(recipe_id, ingredient_id, current_user, db)

    # Verify the food_item exists
    fi_result = await db.execute(
        select(FoodItem).where(FoodItem.id == data.food_item_id)
    )
    fi = fi_result.scalar_one_or_none()
    if not fi:
        raise HTTPException(status_code=404, detail="Food item not found")

    ing.food_item_id = fi.id
    if data.quantity_grams is not None:
        ing.quantity_grams = data.quantity_grams
    await db.flush()

    return {
        "ingredient_id": str(ing.id),
        "food_item_id": str(fi.id),
        "food_item_name": fi.name,
        "quantity_grams": float(ing.quantity_grams) if ing.quantity_grams else None,
    }


@router.delete(
    "/api/v1/recipes/{recipe_id}/ingredients/{ingredient_id}/link",
    status_code=204,
)
async def unlink_ingredient(
    recipe_id: uuid.UUID,
    ingredient_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Remove the food_item link from a recipe ingredient."""
    ing = await _get_ingredient_for_owner(recipe_id, ingredient_id, current_user, db)
    ing.food_item_id = None
    await db.flush()
