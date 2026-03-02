"""add categories to recipe_translations

Revision ID: 0002
Revises: 0001
Create Date: 2025-01-01 00:00:00.000000

Adds a `categories` column to recipe_translations.
Categories are stored per-translation (not per-recipe) because
the same recipe can belong to different category taxonomies
in different languages and culinary cultures.

Examples:
  - IT translation: ["pasta", "primo", "romano"]
  - EN translation: ["pasta", "first_course", "italian"]

Categories are also used as hashtags in ActivityPub federation,
so they appear as searchable tags in Mastodon and other Fediverse
clients.
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
    # Add categories as a PostgreSQL ARRAY of strings.
    # We use ARRAY instead of a join table for the same reasons
    # as dietary_tags: the list is fetched together with the
    # translation, and PostgreSQL GIN indexes handle array search well.
    # server_default='{}' means existing rows get an empty array,
    # not NULL — consistent with dietary_tags behaviour.
    op.add_column(
        "recipe_translations",
        sa.Column(
            "categories",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
    )

    # GIN index for fast array containment queries, e.g.:
    #   WHERE 'pasta' = ANY(categories)
    # Without this index, array lookups would be full table scans.
    op.create_index(
        "ix_recipe_translations_categories",
        "recipe_translations",
        ["categories"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_recipe_translations_categories", table_name="recipe_translations")
    op.drop_column("recipe_translations", "categories")
