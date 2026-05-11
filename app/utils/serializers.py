# ============================================================
# app/utils/serializers.py — recipe serialization helpers
# ============================================================
#
# This module converts our internal Recipe model into standard
# external formats:
#
#   to_schema_org()  → Schema.org/Recipe JSON-LD dict
#                      Used in HTML pages for SEO rich cards
#                      (Google shows prep time, rating, photos
#                       directly in search results)
#
#   to_ap_tags()     → ActivityPub "tag" array
#                      Used in AP Article objects so that
#                      Mastodon and other Fediverse clients
#                      render hashtags and make recipes
#                      searchable across the federation
#
# Neither function touches the database — they are pure
# transformations of data already loaded in memory.
# Call them after loading a Recipe with its relationships.

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid circular imports at runtime; only used for type hints.
    from app.models.recipe import Recipe, RecipeTranslation


# ============================================================
# Internal helpers
# ============================================================

def _seconds_to_iso8601_duration(seconds: int | None) -> str | None:
    """
    Convert a duration in seconds to an ISO 8601 duration string.

    Schema.org and ActivityPub both use ISO 8601 for durations.
    Examples:
        3600  → "PT1H"
        5400  → "PT1H30M"
        1800  → "PT30M"
        90    → "PT1M30S"
    """
    if seconds is None:
        return None

    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = "PT"
    if hours:
        parts += f"{hours}H"
    if minutes:
        parts += f"{minutes}M"
    if secs:
        parts += f"{secs}S"

    # Edge case: exactly 0 seconds
    if parts == "PT":
        return "PT0S"

    return parts


def _normalise_tag(tag: str) -> str:
    """
    Convert a raw tag string to a clean hashtag-safe identifier.

    Rules:
      - lowercase
      - spaces and hyphens become underscores
      - strip leading/trailing whitespace

    Examples:
        "Gluten Free"  → "gluten_free"
        "low-carb"     → "low_carb"
        "vegan"        → "vegan"
    """
    return tag.strip().lower().replace(" ", "_").replace("-", "_")


def _tag_url(instance_domain: str, tag: str) -> str:
    """Build the canonical URL for a hashtag on this instance."""
    return f"https://{instance_domain}/tags/{_normalise_tag(tag)}"


# ============================================================
# Public API
# ============================================================

def to_schema_org(
    recipe: Recipe,
    translation: RecipeTranslation,
    instance_domain: str,
) -> dict:
    """
    Build a Schema.org/Recipe JSON-LD dict from a Recipe and one
    of its translations.

    The caller chooses which translation to use (typically the
    one matching the request's Accept-Language header, or the
    original language as fallback).

    The returned dict should be embedded in the HTML page as:

        <script type="application/ld+json">
        {{ schema_org | tojson }}
        </script>

    References:
        https://schema.org/Recipe
        https://developers.google.com/search/docs/appearance/structured-data/recipe
    """
    recipe_url = f"https://{instance_domain}/users/{recipe.author.username}/recipes/{recipe.slug}"

    # Build the ingredient list in Schema.org format.
    # Schema.org expects plain strings like "200g flour" or "2 eggs".
    # We reconstruct a human-readable string from our structured data.
    ingredients = []
    for ing in recipe.ingredients:
        parts = []
        if ing.quantity is not None:
            # Format: drop trailing zeros (2.0 → "2", 1.5 → "1.5")
            qty = int(ing.quantity) if ing.quantity == int(ing.quantity) else ing.quantity
            parts.append(str(qty))
        if ing.unit and ing.unit != "":
            parts.append(ing.unit)
        parts.append(ing.name)
        if ing.notes:
            parts.append(f"({ing.notes})")
        ingredients.append(" ".join(parts))

    # Build the step list in Schema.org HowToStep format.
    steps = [
        {
            "@type": "HowToStep",
            "position": step.get("order", i + 1),
            "text": step.get("text", ""),
        }
        for i, step in enumerate(translation.steps)
    ]

    # Collect all categories from this translation.
    # Schema.org accepts a single string or a list for recipeCategory.
    categories = translation.categories if translation.categories else []

    # Map our internal dietary tags to Schema.org suitableForDiet values.
    # Schema.org has a controlled vocabulary under https://schema.org/RestrictedDiet
    diet_map = {
        "vegan": "https://schema.org/VeganDiet",
        "vegetarian": "https://schema.org/VegetarianDiet",
        "gluten_free": "https://schema.org/GlutenFreeDiet",
        "halal": "https://schema.org/HalalDiet",
        "kosher": "https://schema.org/KosherDiet",
        "low_calorie": "https://schema.org/LowCalorieDiet",
        "low_fat": "https://schema.org/LowFatDiet",
        "low_lactose": "https://schema.org/LowLactoseDiet",
        "low_salt": "https://schema.org/LowSaltDiet",
    }
    suitable_for_diet = [
        diet_map[tag]
        for tag in (recipe.dietary_tags or [])
        if tag in diet_map
    ]

    # Cover photo (first photo marked as cover, or first photo overall)
    image_urls = []
    if recipe.photos:
        cover = next((p for p in recipe.photos if p.is_cover), recipe.photos[0])
        image_urls = [cover.url]

    result: dict = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": translation.title,
        "url": recipe_url,
        "inLanguage": translation.language,
        "author": {
            "@type": "Person",
            "name": recipe.author.display_name or recipe.author.username,
            "url": f"https://{instance_domain}/users/{recipe.author.username}",
        },
        "datePublished": recipe.published_at.isoformat() if recipe.published_at else None,
        "dateModified": recipe.updated_at.isoformat(),
        "recipeIngredient": ingredients,
        "recipeInstructions": steps,
    }

    # Only add optional fields when we have data — avoids polluting the
    # JSON-LD with null values that some validators flag as warnings.
    if translation.description:
        result["description"] = translation.description
    if categories:
        result["recipeCategory"] = categories
    if image_urls:
        result["image"] = image_urls
    if recipe.prep_time_seconds:
        result["prepTime"] = _seconds_to_iso8601_duration(recipe.prep_time_seconds)
    if recipe.cook_time_seconds:
        result["cookTime"] = _seconds_to_iso8601_duration(recipe.cook_time_seconds)
    if recipe.prep_time_seconds and recipe.cook_time_seconds:
        result["totalTime"] = _seconds_to_iso8601_duration(
            recipe.prep_time_seconds + recipe.cook_time_seconds
        )
    if recipe.servings:
        result["recipeYield"] = str(recipe.servings)
    if suitable_for_diet:
        result["suitableForDiet"] = suitable_for_diet
    if recipe.difficulty:
        # Schema.org has no standard field for difficulty — use a custom property
        result["pasticcio:difficulty"] = recipe.difficulty.value

    return result


def to_ap_tags(
    recipe: Recipe,
    translation: RecipeTranslation,
    instance_domain: str,
) -> list[dict]:
    """
    Build the ActivityPub "tag" array for a Recipe Article object.

    ActivityPub (and Mastodon specifically) uses this array to:
      - Render clickable hashtags in the post body
      - Make the post discoverable via hashtag search across
        the federation (e.g. searching #vegan on Mastodon shows
        posts from Pasticcio instances too)

    We include three categories of tags:
      1. dietary_tags  — from the Recipe (e.g. "vegan", "gluten_free")
      2. metabolic_tags — from the Recipe (e.g. "keto", "low_carb")
      3. categories    — from the specific RecipeTranslation
                         (e.g. "pasta", "primo")

    We intentionally exclude `difficulty` — tags like #easy are
    too generic to be useful in federated search.

    Format per tag (Mastodon-compatible):
        {
            "type": "Hashtag",
            "href": "https://instance.domain/tags/vegan",
            "name": "#vegan"
        }
    """
    # Collect all raw tag strings, deduplicating while preserving order.
    # dict.fromkeys() is an idiomatic Python trick for ordered dedup.
    raw_tags: list[str] = list(dict.fromkeys([
        *( recipe.dietary_tags or []),
        *( recipe.metabolic_tags or []),
        *( translation.categories or []),
    ]))

    return [
        {
            "type": "Hashtag",
            "href": _tag_url(instance_domain, tag),
            "name": f"#{_normalise_tag(tag)}",
        }
        for tag in raw_tags
        if tag  # skip empty strings
    ]
