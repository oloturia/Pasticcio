"""add categories to recipe_translations and recipes

Revision ID: 0002
Revises: 0001
Create Date: 2024-01-02 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add categories array to recipe_translations
    op.add_column(
        "recipe_translations",
        sa.Column("categories", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_recipe_translations_categories ON recipe_translations USING GIN (categories)"
    )

    # Add categories array to recipes as well
    op.add_column(
        "recipes",
        sa.Column("categories", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("recipes", "categories")
    op.drop_index("ix_recipe_translations_categories", table_name="recipe_translations")
    op.drop_column("recipe_translations", "categories")
