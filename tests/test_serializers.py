# ============================================================
# tests/test_serializers.py — unit tests for recipe serializers
# ============================================================
#
# These are pure unit tests: no database, no HTTP client.
# We build fake Recipe/Translation objects using SimpleNamespace
# (a lightweight "object with attributes") and pass them directly
# to the serializer functions.
#
# This is faster than integration tests and makes it easy to
# test edge cases (missing fields, empty lists, etc.).

from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from app.utils.serializers import (
    _seconds_to_iso8601_duration,
    _normalise_tag,
    to_schema_org,
    to_ap_tags,
)


# ============================================================
# Helpers
# ============================================================

DOMAIN = "pasticcio.example.org"


def make_author(**kwargs):
    defaults = dict(username="chefmaria", display_name="Maria Rossi")
    return SimpleNamespace(**{**defaults, **kwargs})


def make_translation(**kwargs):
    defaults = dict(
        language="it",
        title="Pasta al Pomodoro",
        description="Un classico intramontabile.",
        steps=[
            {"order": 1, "text": "Portare l'acqua a ebollizione."},
            {"order": 2, "text": "Cuocere la pasta al dente."},
        ],
        categories=["pasta", "primo", "italiano"],
    )
    return SimpleNamespace(**{**defaults, **kwargs})


def make_ingredient(**kwargs):
    defaults = dict(quantity=200.0, unit="g", name="spaghetti", notes=None)
    return SimpleNamespace(**{**defaults, **kwargs})


def make_recipe(**kwargs):
    defaults = dict(
        slug="pasta-al-pomodoro",
        author=make_author(),
        dietary_tags=["vegan"],
        metabolic_tags=["low_fat"],
        photos=[],
        ingredients=[make_ingredient()],
        prep_time_seconds=600,
        cook_time_seconds=1200,
        servings=4,
        difficulty=SimpleNamespace(value="easy"),
        published_at=datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc),
        updated_at=datetime(2024, 6, 15, 8, 0, tzinfo=timezone.utc),
    )
    return SimpleNamespace(**{**defaults, **kwargs})


# ============================================================
# _seconds_to_iso8601_duration
# ============================================================

def test_duration_hours_only():
    assert _seconds_to_iso8601_duration(3600) == "PT1H"

def test_duration_minutes_only():
    assert _seconds_to_iso8601_duration(1800) == "PT30M"

def test_duration_hours_and_minutes():
    assert _seconds_to_iso8601_duration(5400) == "PT1H30M"

def test_duration_all_components():
    assert _seconds_to_iso8601_duration(3661) == "PT1H1M1S"

def test_duration_seconds_only():
    assert _seconds_to_iso8601_duration(45) == "PT45S"

def test_duration_zero():
    assert _seconds_to_iso8601_duration(0) == "PT0S"

def test_duration_none():
    assert _seconds_to_iso8601_duration(None) is None


# ============================================================
# _normalise_tag
# ============================================================

def test_normalise_tag_lowercase():
    assert _normalise_tag("Vegan") == "vegan"

def test_normalise_tag_spaces_to_underscore():
    assert _normalise_tag("Gluten Free") == "gluten_free"

def test_normalise_tag_hyphens_to_underscore():
    assert _normalise_tag("low-carb") == "low_carb"

def test_normalise_tag_strips_whitespace():
    assert _normalise_tag("  pasta  ") == "pasta"


# ============================================================
# to_schema_org
# ============================================================

def test_schema_org_type_and_context():
    recipe = make_recipe()
    translation = make_translation()
    result = to_schema_org(recipe, translation, DOMAIN)
    assert result["@context"] == "https://schema.org"
    assert result["@type"] == "Recipe"

def test_schema_org_basic_fields():
    recipe = make_recipe()
    translation = make_translation()
    result = to_schema_org(recipe, translation, DOMAIN)
    assert result["name"] == "Pasta al Pomodoro"
    assert result["inLanguage"] == "it"
    assert DOMAIN in result["url"]
    assert "chefmaria" in result["url"]
    assert "pasta-al-pomodoro" in result["url"]

def test_schema_org_author():
    recipe = make_recipe()
    result = to_schema_org(recipe, make_translation(), DOMAIN)
    assert result["author"]["@type"] == "Person"
    assert result["author"]["name"] == "Maria Rossi"

def test_schema_org_author_falls_back_to_username():
    recipe = make_recipe(author=make_author(display_name=None))
    result = to_schema_org(recipe, make_translation(), DOMAIN)
    assert result["author"]["name"] == "chefmaria"

def test_schema_org_ingredients_formatted():
    ingredient = make_ingredient(quantity=200.0, unit="g", name="spaghetti", notes=None)
    recipe = make_recipe(ingredients=[ingredient])
    result = to_schema_org(recipe, make_translation(), DOMAIN)
    assert "200 g spaghetti" in result["recipeIngredient"]

def test_schema_org_ingredient_with_notes():
    ingredient = make_ingredient(quantity=2.0, unit="", name="uova", notes="a temperatura ambiente")
    recipe = make_recipe(ingredients=[ingredient])
    result = to_schema_org(recipe, make_translation(), DOMAIN)
    assert "(a temperatura ambiente)" in result["recipeIngredient"][0]

def test_schema_org_ingredient_no_quantity():
    ingredient = make_ingredient(quantity=None, unit="", name="sale", notes=None)
    recipe = make_recipe(ingredients=[ingredient])
    result = to_schema_org(recipe, make_translation(), DOMAIN)
    assert result["recipeIngredient"][0] == "sale"

def test_schema_org_steps():
    result = to_schema_org(make_recipe(), make_translation(), DOMAIN)
    assert len(result["recipeInstructions"]) == 2
    assert result["recipeInstructions"][0]["@type"] == "HowToStep"
    assert result["recipeInstructions"][0]["position"] == 1

def test_schema_org_times():
    result = to_schema_org(make_recipe(), make_translation(), DOMAIN)
    assert result["prepTime"] == "PT10M"
    assert result["cookTime"] == "PT20M"
    assert result["totalTime"] == "PT30M"

def test_schema_org_servings():
    result = to_schema_org(make_recipe(), make_translation(), DOMAIN)
    assert result["recipeYield"] == "4"

def test_schema_org_categories():
    result = to_schema_org(make_recipe(), make_translation(), DOMAIN)
    assert result["recipeCategory"] == ["pasta", "primo", "italiano"]

def test_schema_org_suitable_for_diet_vegan():
    result = to_schema_org(make_recipe(), make_translation(), DOMAIN)
    assert "https://schema.org/VeganDiet" in result["suitableForDiet"]

def test_schema_org_suitable_for_diet_unknown_tag_ignored():
    recipe = make_recipe(dietary_tags=["raw_food"])  # not in our map
    result = to_schema_org(recipe, make_translation(), DOMAIN)
    assert "suitableForDiet" not in result

def test_schema_org_description_present_when_set():
    translation = make_translation(description="Un classico intramontabile.")
    result = to_schema_org(make_recipe(), translation, DOMAIN)
    assert "description" in result
    assert result["description"] == "Un classico intramontabile."

def test_schema_org_description_omitted_when_none():
    translation = make_translation(description=None)
    result = to_schema_org(make_recipe(), translation, DOMAIN)
    assert "description" not in result

def test_schema_org_empty_categories_field_omitted():
    translation = make_translation(categories=[])
    result = to_schema_org(make_recipe(), translation, DOMAIN)
    assert "recipeCategory" not in result

def test_schema_org_cover_photo():
    photo = SimpleNamespace(url="https://cdn.example.org/photo.jpg", is_cover=True)
    recipe = make_recipe(photos=[photo])
    result = to_schema_org(recipe, make_translation(), DOMAIN)
    assert result["image"] == ["https://cdn.example.org/photo.jpg"]

def test_schema_org_no_photos_field_omitted():
    result = to_schema_org(make_recipe(photos=[]), make_translation(), DOMAIN)
    assert "image" not in result


# ============================================================
# to_ap_tags
# ============================================================

def test_ap_tags_returns_list():
    result = to_ap_tags(make_recipe(), make_translation(), DOMAIN)
    assert isinstance(result, list)

def test_ap_tags_all_sources_included():
    # dietary_tags=["vegan"], metabolic_tags=["low_fat"], categories=["pasta","primo","italiano"]
    result = to_ap_tags(make_recipe(), make_translation(), DOMAIN)
    names = [t["name"] for t in result]
    assert "#vegan" in names
    assert "#low_fat" in names
    assert "#pasta" in names
    assert "#primo" in names
    assert "#italiano" in names

def test_ap_tags_type_is_hashtag():
    result = to_ap_tags(make_recipe(), make_translation(), DOMAIN)
    assert all(t["type"] == "Hashtag" for t in result)

def test_ap_tags_href_contains_domain():
    result = to_ap_tags(make_recipe(), make_translation(), DOMAIN)
    assert all(DOMAIN in t["href"] for t in result)

def test_ap_tags_name_has_hash_prefix():
    result = to_ap_tags(make_recipe(), make_translation(), DOMAIN)
    assert all(t["name"].startswith("#") for t in result)

def test_ap_tags_deduplication():
    # If dietary_tags and categories share a value, it should appear only once
    recipe = make_recipe(dietary_tags=["vegan"], metabolic_tags=[])
    translation = make_translation(categories=["vegan", "pasta"])  # "vegan" duplicated
    result = to_ap_tags(recipe, translation, DOMAIN)
    names = [t["name"] for t in result]
    assert names.count("#vegan") == 1

def test_ap_tags_normalises_spaces():
    recipe = make_recipe(dietary_tags=["gluten free"], metabolic_tags=[])
    translation = make_translation(categories=[])
    result = to_ap_tags(recipe, translation, DOMAIN)
    assert result[0]["name"] == "#gluten_free"

def test_ap_tags_empty_recipe():
    recipe = make_recipe(dietary_tags=[], metabolic_tags=[])
    translation = make_translation(categories=[])
    result = to_ap_tags(recipe, translation, DOMAIN)
    assert result == []

def test_ap_tags_difficulty_not_included():
    # difficulty should never appear in AP tags — too generic
    result = to_ap_tags(make_recipe(), make_translation(), DOMAIN)
    names = [t["name"] for t in result]
    assert "#easy" not in names
    assert "#medium" not in names
    assert "#hard" not in names
