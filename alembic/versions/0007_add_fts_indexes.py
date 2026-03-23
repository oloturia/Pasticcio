"""add full-text search indexes

Revision ID: 0007
Revises: 0006
Create Date: 2024-01-07 00:00:00.000000

Adds PostgreSQL GIN indexes for full-text search on:
  - recipe_translations (title + description)
  - recipe_ingredients (name)

We use to_tsvector with 'simple' configuration (no language-specific
stemming) so that multilingual content works reasonably well.
Language-specific search can be added later per translation language.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Full-text index on recipe title + description
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_recipe_translations_fts
        ON recipe_translations
        USING GIN (to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(description, '')))
    """)

    # Full-text index on ingredient names
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_recipe_ingredients_fts
        ON recipe_ingredients
        USING GIN (to_tsvector('simple', name))
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_recipe_translations_fts")
    op.execute("DROP INDEX IF EXISTS ix_recipe_ingredients_fts")
