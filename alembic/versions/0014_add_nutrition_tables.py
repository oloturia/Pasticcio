"""add_nutrition_tables

Revision ID: 7053601ef324
Revises: 0013
Create Date: 2026-04-21 11:03:39.235602+00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB 

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add gram conversion column to recipe_ingredients
    # This will cache the conversion of quantity+unit to grams
    op.add_column('recipe_ingredients', 
        sa.Column('quantity_grams', sa.Numeric(10, 3), nullable=True,
                  comment='Cached conversion of quantity+unit to grams for nutrition calculations'))
    
    # Add index for faster nutrition calculations
    op.create_index('ix_recipe_ingredients_food_item_id', 
                    'recipe_ingredients', ['food_item_id'])
    
    # Create recipe_nutrition table for cached totals
    op.create_table(
        'recipe_nutrition',
        sa.Column('id', UUID(as_uuid=True), nullable=False, 
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('recipe_id', UUID(as_uuid=True), nullable=False),
        sa.Column('servings', sa.Integer(), nullable=False, server_default='4'),
        
        # Store all nutrition data as JSONB to match your existing pattern
        # Structure: {"calories": 250, "protein": 12.5, "carbohydrates": 30, ...}
        sa.Column('total_nutrition', JSONB, nullable=True,
                  comment='Total nutritional values for entire recipe'),
        sa.Column('per_serving_nutrition', JSONB, nullable=True,
                  comment='Nutritional values per serving (total / servings)'),
        
        # Metadata
        sa.Column('last_calculated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), 
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), 
                  server_default=sa.text('now()'), nullable=False),
        
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['recipe_id'], ['recipes.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('recipe_id', name='uq_recipe_nutrition_recipe'),
    )
    
    # Add index for recipe lookups
    op.create_index('ix_recipe_nutrition_recipe_id', 
                    'recipe_nutrition', ['recipe_id'])


def downgrade() -> None:
    # Drop indexes and table
    op.drop_index('ix_recipe_nutrition_recipe_id', table_name='recipe_nutrition')
    op.drop_table('recipe_nutrition')
    op.drop_index('ix_recipe_ingredients_food_item_id', table_name='recipe_ingredients')
    op.drop_column('recipe_ingredients', 'quantity_grams')
