# ============================================================
# app/models/recipe.py — Recipe and related models
# ============================================================
#
# A recipe is the core object of Pasticcio. This file defines:
#   - Recipe          the main recipe record
#   - RecipeTranslation  a translated version of a recipe
#   - RecipeIngredient   one ingredient line in a recipe
#   - RecipePhoto        an attached photo
#
# More models (CookedThis, FoodItem) will be added later.

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ============================================================
# Enums
# ============================================================
# Python enums map to PostgreSQL ENUM types.
# Using enums instead of plain strings means the database
# enforces valid values — you can't accidentally store "Vgean".

class RecipeStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    UNLISTED = "unlisted"
    DELETED = "deleted"


class TranslationStatus(str, enum.Enum):
    ORIGINAL = "original"
    DRAFT = "draft"
    REVIEWED = "reviewed"


class Difficulty(str, enum.Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class IngredientUnit(str, enum.Enum):
    GRAM = "g"
    KILOGRAM = "kg"
    OUNCE = "oz"
    POUND = "lb"
    MILLILITER = "ml"
    LITER = "l"
    TEASPOON = "tsp"
    TABLESPOON = "tbsp"
    CUP = "cup"
    FLUID_OUNCE = "fl_oz"
    PIECE = "piece"
    PINCH = "pinch"
    TO_TASTE = "to_taste"
    NONE = ""


# ============================================================
# Recipe
# ============================================================

class FoodItem(Base):
    __tablename__ = "food_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    names: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    per_100g: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # --- Author ---
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author: Mapped["User"] = relationship("User", back_populates="recipes")  # noqa: F821

    # --- Identity ---
    # slug is a URL-friendly version of the title, e.g. "pasta-al-pesto"
    # unique together with author_id: two authors can have a recipe
    # with the same slug, but one author can't have two with the same slug.
    slug: Mapped[str] = mapped_column(String(256), nullable=False, index=True)

    # ActivityPub ID — the canonical URL of this recipe in the Fediverse
    ap_id: Mapped[str] = mapped_column(String(512), unique=True, nullable=False, index=True)

    # --- Status and visibility ---
    status: Mapped[RecipeStatus] = mapped_column(
        Enum(RecipeStatus, name="recipestatustype"), default=RecipeStatus.DRAFT, nullable=False, index=True
    )

    # --- Language ---
    # The language the recipe was originally written in (BCP-47 code,
    # e.g. "en", "it", "fr"). Translations are in RecipeTranslation.
    original_language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")

    # --- Cooking metadata ---
    # Times are stored in seconds (simple integer, easy to format later)
    prep_time_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cook_time_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    servings: Mapped[int | None] = mapped_column(Integer, nullable=True)
    difficulty: Mapped[Difficulty | None] = mapped_column(Enum(Difficulty, name="difficulttype"), nullable=True)

    # --- Dietary tags ---
    # Stored as a PostgreSQL ARRAY of strings.
    # Examples: ["vegan", "gluten_free"]
    # We use an array instead of a separate join table because:
    # - the list of tags is small and fixed
    # - we often need ALL tags at once (no benefit from lazy loading)
    # - PostgreSQL can index arrays efficiently with GIN indexes
    dietary_tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    metabolic_tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)

    # When any metabolic tag is set, the UI must show the disclaimer.
    # We store this explicitly so the frontend doesn't need to guess.
    show_metabolic_disclaimer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- ActivityPub cache ---
    # The full AP JSON representation of this recipe, cached here
    # so we don't have to rebuild it from scratch on every federation
    # request. Invalidated whenever the recipe is updated.
    ap_object: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Relationships ---
    translations: Mapped[list["RecipeTranslation"]] = relationship(
        "RecipeTranslation",
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="RecipeTranslation.language",
    )
    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        "RecipeIngredient",
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="RecipeIngredient.sort_order",
    )
    photos: Mapped[list["RecipePhoto"]] = relationship(
        "RecipePhoto",
        back_populates="recipe",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Recipe {self.ap_id}>"


# ============================================================
# RecipeTranslation
# ============================================================

class RecipeTranslation(Base):
    __tablename__ = "recipe_translations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipe: Mapped["Recipe"] = relationship("Recipe", back_populates="translations")

    # BCP-47 language code: "en", "it", "fr", "de", ...
    language: Mapped[str] = mapped_column(String(10), nullable=False)

    # --- Translatable content ---
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Steps are stored as a JSON array of objects:
    # [{"order": 1, "text": "Boil the water"}, ...]
    # This avoids a separate steps table for now. If we need
    # per-step media attachments later, we can migrate to a table.
    steps: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)

    # --- Translation metadata ---
    status: Mapped[TranslationStatus] = mapped_column(
        Enum(TranslationStatus, name="translationstatustype"),
        default=TranslationStatus.DRAFT,
        nullable=False,
    )

    # Who translated this (null if it's the original language version)
    translated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    translated_by: Mapped["User | None"] = relationship("User", foreign_keys=[translated_by_id])  # noqa: F821

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<RecipeTranslation {self.recipe_id} [{self.language}]>"


# ============================================================
# RecipeIngredient
# ============================================================

class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipe: Mapped["Recipe"] = relationship("Recipe", back_populates="ingredients")

    # Position in the ingredient list (1, 2, 3, ...)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- Ingredient data ---
    # Quantity uses Numeric (exact decimal) instead of Float
    # to avoid floating point weirdness (0.1 + 0.2 ≠ 0.3 in floats).
    quantity: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    unit: Mapped[IngredientUnit] = mapped_column(
        Enum(IngredientUnit, name="ingredientunittype"), default=IngredientUnit.NONE, nullable=False
    )

    # Free-text name in the recipe's original language
    name: Mapped[str] = mapped_column(String(256), nullable=False)

    # Optional note (e.g. "finely chopped", "at room temperature")
    notes: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Link to the nutritional database (optional feature).
    # If None, no nutritional data is available for this ingredient.
    # ForeignKey points to a table we'll create later when we build
    # the nutrition module — using SET NULL so missing the table
    # doesn't break anything.
    food_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("food_items.id", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<RecipeIngredient {self.quantity} {self.unit} {self.name}>"


# ============================================================
# RecipePhoto
# ============================================================

class RecipePhoto(Base):
    __tablename__ = "recipe_photos"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipe: Mapped["Recipe"] = relationship("Recipe", back_populates="photos")

    # Path relative to MEDIA_ROOT (e.g. "recipes/uuid/photo.jpg")
    # We store a relative path, not an absolute URL, so the instance
    # domain can change without breaking all photo references.
    url: Mapped[str] = mapped_column(String(512), nullable=False)

    # Alt text for accessibility and screen readers
    alt_text: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Only one photo per recipe can be the cover image
    is_cover: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<RecipePhoto {self.url}>"
