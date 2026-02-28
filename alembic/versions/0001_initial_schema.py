"""initial schema — users and recipes

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000

This is the first migration. It creates the core tables:
  - users
  - recipes
  - recipe_translations
  - recipe_ingredients
  - recipe_photos

A placeholder food_items table is also created (empty for now)
because recipe_ingredients has a foreign key to it.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None  # None means "this is the first migration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- ENUM types ---
    # PostgreSQL enums must be created before the tables that use them.
    # We create them explicitly so downgrade() can drop them cleanly.

    recipe_status = postgresql.ENUM(
        "draft", "published", "unlisted", "deleted",
        name="recipestatus",
        create_type=False,
    )
    recipe_status.create(op.get_bind(), checkfirst=True)

    translation_status = postgresql.ENUM(
        "original", "draft", "reviewed",
        name="translationstatus",
        create_type=False,
    )
    translation_status.create(op.get_bind(), checkfirst=True)

    difficulty = postgresql.ENUM(
        "easy", "medium", "hard",
        name="difficulty",
        create_type=False,
    )
    difficulty.create(op.get_bind(), checkfirst=True)

    ingredient_unit = postgresql.ENUM(
        "g", "kg", "oz", "lb",
        "ml", "l", "tsp", "tbsp", "cup", "fl_oz",
        "piece", "pinch", "to_taste", "",
        name="ingredientunit",
        create_type=False,
    )
    ingredient_unit.create(op.get_bind(), checkfirst=True)

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.String(512), nullable=True),
        sa.Column("ap_id", sa.String(512), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=True),
        sa.Column("private_key", sa.Text(), nullable=True),
        sa.Column("is_remote", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("remote_actor_url", sa.String(512), nullable=True),
        sa.Column("email", sa.String(256), nullable=True),
        sa.Column("hashed_password", sa.String(256), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("preferred_language", sa.String(10), nullable=False, server_default="en"),
        sa.Column("settings", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_ap_id", "users", ["ap_id"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # --- food_items (placeholder for the nutrition module) ---
    # Created now because recipe_ingredients references it.
    # Will be populated when we build the nutrition module.
    op.create_table(
        "food_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("names", postgresql.JSONB(), nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("source_id", sa.String(128), nullable=True),
        sa.Column("per_100g", postgresql.JSONB(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- recipes ---
    op.create_table(
        "recipes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.String(256), nullable=False),
        sa.Column("ap_id", sa.String(512), nullable=False),
        sa.Column("status", sa.Enum("draft", "published", "unlisted", "deleted", name="recipestatus"), nullable=False, server_default="draft"),
        sa.Column("original_language", sa.String(10), nullable=False, server_default="en"),
        sa.Column("prep_time_seconds", sa.Integer(), nullable=True),
        sa.Column("cook_time_seconds", sa.Integer(), nullable=True),
        sa.Column("servings", sa.Integer(), nullable=True),
        sa.Column("difficulty", sa.Enum("easy", "medium", "hard", name="difficulty"), nullable=True),
        sa.Column("dietary_tags", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("metabolic_tags", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("show_metabolic_disclaimer", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("ap_object", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_recipes_author_id", "recipes", ["author_id"])
    op.create_index("ix_recipes_ap_id", "recipes", ["ap_id"], unique=True)
    op.create_index("ix_recipes_status", "recipes", ["status"])
    # Composite unique constraint: one slug per author
    op.create_index("ix_recipes_author_slug", "recipes", ["author_id", "slug"], unique=True)
    # GIN index on arrays: allows fast filtering by dietary_tags
    op.create_index("ix_recipes_dietary_tags", "recipes", ["dietary_tags"], postgresql_using="gin")

    # --- recipe_translations ---
    op.create_table(
        "recipe_translations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recipe_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("steps", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.Enum("original", "draft", "reviewed", name="translationstatus"), nullable=False, server_default="draft"),
        sa.Column("translated_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_recipe_translations_recipe_id", "recipe_translations", ["recipe_id"])
    # One translation per language per recipe
    op.create_index("ix_recipe_translations_recipe_language", "recipe_translations", ["recipe_id", "language"], unique=True)

    # --- recipe_ingredients ---
    op.create_table(
        "recipe_ingredients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recipe_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=True),
        sa.Column("unit", sa.Enum("g", "kg", "oz", "lb", "ml", "l", "tsp", "tbsp", "cup", "fl_oz", "piece", "pinch", "to_taste", "", name="ingredientunit"), nullable=False, server_default=""),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("notes", sa.String(256), nullable=True),
        sa.Column("food_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("food_items.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_recipe_ingredients_recipe_id", "recipe_ingredients", ["recipe_id"])

    # --- recipe_photos ---
    op.create_table(
        "recipe_photos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recipe_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("alt_text", sa.String(256), nullable=True),
        sa.Column("is_cover", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_recipe_photos_recipe_id", "recipe_photos", ["recipe_id"])


def downgrade() -> None:
    # Drop tables in reverse order (children before parents,
    # to avoid foreign key constraint violations)
    op.drop_table("recipe_photos")
    op.drop_table("recipe_ingredients")
    op.drop_table("recipe_translations")
    op.drop_table("recipes")
    op.drop_table("food_items")
    op.drop_table("users")

    # Drop enum types last
    op.execute("DROP TYPE IF EXISTS ingredientunit")
    op.execute("DROP TYPE IF EXISTS difficulty")
    op.execute("DROP TYPE IF EXISTS translationstatus")
    op.execute("DROP TYPE IF EXISTS recipestatus")
