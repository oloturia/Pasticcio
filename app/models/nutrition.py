import sqlalchemy as sa
from sqlalchemy import Column, Integer, Numeric, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from app.database import Base
from app.models.recipe import FoodItem 
from app.models.recipe import RecipeIngredient

class RecipeNutrition(Base):
    """
    Cached nutritional values for entire recipe and per serving.
    Uses JSONB to store flexible nutrition data matching food_items pattern.
    """
    __tablename__ = "recipe_nutrition"
    __table_args__ = (
        UniqueConstraint('recipe_id', name='uq_recipe_nutrition_recipe'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipe_id = Column(UUID(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"), 
                       nullable=False, unique=True, index=True)
    
    servings = Column(Integer, nullable=False, server_default='4')
    
    # JSONB columns for nutrition data (flexible schema)
    total_nutrition = Column(JSONB, nullable=True)
    per_serving_nutrition = Column(JSONB, nullable=True)
    
    last_calculated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    recipe = relationship("Recipe", back_populates="nutrition")

    def __repr__(self):
        calories = self.per_serving_nutrition.get('calories', 0) if self.per_serving_nutrition else 0
        return f"<RecipeNutrition(recipe_id={self.recipe_id}, calories_per_serving={calories})>"
    
    def get_total_nutrient(self, nutrient_name: str) -> float:
        """Get total nutrient value for entire recipe"""
        if not self.total_nutrition:
            return 0.0
        return float(self.total_nutrition.get(nutrient_name, 0.0))
    
    def get_per_serving_nutrient(self, nutrient_name: str) -> float:
        """Get nutrient value per serving"""
        if not self.per_serving_nutrition:
            return 0.0
        return float(self.per_serving_nutrition.get(nutrient_name, 0.0))
