"""
API endpoints for recipe nutrition calculations.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from pydantic import BaseModel, Field

from app.database import get_db
from app.services.nutrition_calculator import NutritionCalculator
from app.routers.auth import get_current_user  # Your auth dependency


router = APIRouter(prefix="/api/v1/recipes", tags=["nutrition"])


class NutritionResponse(BaseModel):
    """Response model for nutrition data"""
    total: dict = Field(..., description="Total nutritional values for entire recipe")
    per_serving: dict = Field(..., description="Nutritional values per serving")
    servings: int = Field(..., description="Number of servings")
    ingredients_with_nutrition: Optional[int] = Field(None, description="Count of ingredients with nutrition data")
    ingredients_without_nutrition: Optional[int] = Field(None, description="Count of ingredients without nutrition data")
    coverage_percentage: Optional[float] = Field(None, description="Percentage of ingredients with nutrition data")
    last_calculated_at: Optional[str] = Field(None, description="When nutrition was last calculated")
    from_cache: bool = Field(..., description="Whether data came from cache")
    
    class Config:
        json_schema_extra = {
            "example": {
                "total": {
                    "calories": 1200.5,
                    "protein": 45.2,
                    "carbohydrates": 180.3,
                    "fat": 25.1,
                    "fiber": 8.5,
                    "sugar": 12.3,
                    "sodium": 850.0,
                    "cholesterol": 120.0,
                    "saturated_fat": 8.2
                },
                "per_serving": {
                    "calories": 300.1,
                    "protein": 11.3,
                    "carbohydrates": 45.1,
                    "fat": 6.3,
                    "fiber": 2.1,
                    "sugar": 3.1,
                    "sodium": 212.5,
                    "cholesterol": 30.0,
                    "saturated_fat": 2.1
                },
                "servings": 4,
                "ingredients_with_nutrition": 8,
                "ingredients_without_nutrition": 2,
                "coverage_percentage": 80.0,
                "from_cache": False
            }
        }


@router.get("/{recipe_id}/nutrition", response_model=NutritionResponse)
async def get_recipe_nutrition(
    recipe_id: str,
    recalculate: bool = Query(False, description="Force recalculation even if cached"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get nutritional information for a recipe.
    
    Returns cached data if available, otherwise calculates fresh.
    Use recalculate=true to force fresh calculation.
    """
    try:
        calculator = NutritionCalculator(db)
        nutrition = await calculator.get_or_calculate_nutrition(
            recipe_id,
            force_recalculate=recalculate
        )
        return nutrition
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calculating nutrition: {str(e)}")


@router.post("/{recipe_id}/nutrition/recalculate", response_model=NutritionResponse)
async def recalculate_recipe_nutrition(
    recipe_id: str,
    servings: Optional[int] = Query(None, description="Override number of servings"),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)  # Require authentication
):
    """
    Force recalculation of recipe nutrition.
    Requires authentication.
    """
    try:
        calculator = NutritionCalculator(db)
        
        # Calculate with optional servings override
        if servings:
            nutrition = await calculator.calculate_recipe_nutrition(recipe_id, servings)
        else:
            nutrition = await calculator.calculate_recipe_nutrition(recipe_id)
        
        # Save to cache
        await calculator.save_nutrition_cache(recipe_id, nutrition)
        
        nutrition["from_cache"] = False
        return nutrition
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error recalculating nutrition: {str(e)}")
