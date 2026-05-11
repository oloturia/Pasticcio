"""
Service for calculating recipe nutritional values.
Aggregates nutrition from all ingredients and caches results.
"""
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.nutrition import RecipeNutrition
from app.models.recipe import Recipe, RecipeIngredient


# Conversion factors from common units to grams.
# For liquid units we approximate density = 1 (water-like).
UNIT_TO_GRAMS = {
    "g":      1.0,
    "kg":     1000.0,
    "oz":     28.3495,
    "lb":     453.592,
    "ml":     1.0,
    "l":      1000.0,
    "tsp":    4.2,
    "tbsp":   12.6,
    "cup":    240.0,
    "fl_oz":  29.5735,
    "piece":  None,   # cannot convert without density — skip
    "pinch":  0.3,
    "to_taste": None, # skip
    "":       None,   # bare count — skip
}

# Keys in our food_items.per_100g JSONB
# Populated by the seed script from USDA data
# Keys in our food_items.per_100g JSONB
# Populated by the seed script from USDA data
NUTRIENT_KEYS = [
    "kcal", "protein_g", "fat_g", "carbs_g", "fiber_g",
    "sugar_g", "saturated_fat_g", "cholesterol_mg", "sodium_mg",
]

# Mapping to the response keys the frontend expects
DB_TO_RESPONSE = {
    "kcal":             "calories",
    "protein_g":        "protein",
    "fat_g":            "fat",
    "carbs_g":          "carbohydrates",
    "fiber_g":          "fiber",
    "sugar_g":          "sugar",
    "saturated_fat_g":  "saturated_fat",
    "cholesterol_mg":   "cholesterol",
    "sodium_mg":        "sodium",
}


def _to_grams(quantity: float | None, unit: str | None) -> float | None:
    """
    Convert an ingredient quantity to grams.
    Returns None if the unit cannot be converted (piece, to_taste, etc.).
    """
    if quantity is None or quantity == 0:
        return None
    unit_key = (unit or "").lower().strip()
    factor = UNIT_TO_GRAMS.get(unit_key)
    if factor is None:
        return None
    return float(quantity) * factor


class NutritionCalculator:
    """Calculates nutritional values for recipes from linked FoodItem data."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def calculate_recipe_nutrition(
        self,
        recipe_id: str,
        servings: Optional[int] = None,
    ) -> dict:
        """
        Calculate nutritional values for a recipe.

        Returns a dict with:
          total, per_serving, servings,
          ingredients_with_nutrition, ingredients_without_nutrition,
          coverage_percentage
        """
        stmt = (
            select(Recipe)
            .options(
                selectinload(Recipe.ingredients).selectinload(RecipeIngredient.food_item)
            )
            .where(Recipe.id == recipe_id)
        )
        result = await self.db.execute(stmt)
        recipe = result.scalar_one_or_none()

        if not recipe:
            raise ValueError(f"Recipe {recipe_id} not found")

        servings = servings or recipe.servings or 1

        # Accumulate totals
        totals = {k: 0.0 for k in NUTRIENT_KEYS}
        with_nutrition = 0
        without_nutrition = 0

        for ing in recipe.ingredients:
            if not ing.food_item or not ing.food_item.per_100g:
                without_nutrition += 1
                continue

            # Get unit value — SQLAlchemy Enum or plain string
            # Use manual override if set, otherwise convert from unit
            if ing.quantity_grams:
                grams = float(ing.quantity_grams)
            else:
                unit_val = ing.unit.value if hasattr(ing.unit, "value") else str(ing.unit or "")
                grams = _to_grams(ing.quantity, unit_val)

            if grams is None:
                without_nutrition += 1
                continue
                
            multiplier = grams / 100.0
            per_100g = ing.food_item.per_100g

            for key in NUTRIENT_KEYS:
                value = per_100g.get(key)
                if value is not None:
                    totals[key] += float(value) * multiplier

            with_nutrition += 1

        # Round and build response using frontend-friendly keys
        total_resp = {
            DB_TO_RESPONSE[k]: round(v, 2)
            for k, v in totals.items()
        }
        per_serving_resp = {
            DB_TO_RESPONSE[k]: round(v / servings, 2)
            for k, v in totals.items()
        }

        total_ingredients = with_nutrition + without_nutrition
        coverage = (with_nutrition / total_ingredients * 100) if total_ingredients else 0.0

        return {
            "total": total_resp,
            "per_serving": per_serving_resp,
            "servings": servings,
            "ingredients_with_nutrition": with_nutrition,
            "ingredients_without_nutrition": without_nutrition,
            "coverage_percentage": round(coverage, 1),
        }

    async def save_nutrition_cache(self, recipe_id: str, nutrition_data: dict) -> RecipeNutrition:
        """Save or update the RecipeNutrition cache row."""
        stmt = select(RecipeNutrition).where(RecipeNutrition.recipe_id == recipe_id)
        result = await self.db.execute(stmt)
        cached = result.scalar_one_or_none()

        now = datetime.now(timezone.utc)

        if cached:
            cached.servings = nutrition_data["servings"]
            cached.total_nutrition = nutrition_data["total"]
            cached.per_serving_nutrition = nutrition_data["per_serving"]
            cached.last_calculated_at = now
        else:
            cached = RecipeNutrition(
                recipe_id=recipe_id,
                servings=nutrition_data["servings"],
                total_nutrition=nutrition_data["total"],
                per_serving_nutrition=nutrition_data["per_serving"],
                last_calculated_at=now,
            )
            self.db.add(cached)

        await self.db.flush()
        return cached

    async def get_or_calculate_nutrition(
        self,
        recipe_id: str,
        force_recalculate: bool = False,
    ) -> dict:
        """Return cached nutrition or calculate fresh."""
        if not force_recalculate:
            stmt = select(RecipeNutrition).where(RecipeNutrition.recipe_id == recipe_id)
            result = await self.db.execute(stmt)
            cached = result.scalar_one_or_none()

            if cached and cached.total_nutrition:
                return {
                    "total": cached.total_nutrition,
                    "per_serving": cached.per_serving_nutrition,
                    "servings": cached.servings,
                    "last_calculated_at": (
                        cached.last_calculated_at.isoformat()
                        if cached.last_calculated_at else None
                    ),
                    "from_cache": True,
                }

        nutrition_data = await self.calculate_recipe_nutrition(recipe_id)
        await self.save_nutrition_cache(recipe_id, nutrition_data)
        nutrition_data["from_cache"] = False
        return nutrition_data
