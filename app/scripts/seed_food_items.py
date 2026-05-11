#!/usr/bin/env python3
# ============================================================
# scripts/seed_food_items.py — import USDA FoodData Central
# ============================================================
#
# Reads Foundation Foods and SR Legacy JSON files and populates
# the food_items table with per-100g nutritional data.
#
# Only the five core nutrients are imported:
#   kcal, protein, fat, carbohydrates, fiber
#
# Usage (run inside the container):
#   python scripts/seed_food_items.py
#
# The script is idempotent: existing records (matched by source_id)
# are updated, not duplicated.

import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# ── Configuration ───────────────────────────────────────────

# Paths to the JSON files relative to the project root
FOOD_DATA_DIR = Path("app/FoodData")

FILES = [
    ("foundation", FOOD_DATA_DIR / "FoodData_Central_foundation_food_json.json"),
    ("sr_legacy",  FOOD_DATA_DIR / "FoodData_Central_sr_legacy_food_json.json"),
]

# USDA nutrient names → our storage keys in per_100g JSONB.
# Keys match what nutrition_calculator.py expects.
# USDA uses slightly different names across datasets — map all known variants.
NUTRIENT_MAP = {
    "Energy":                              "kcal",
    "Protein":                             "protein_g",
    "Total lipid (fat)":                   "fat_g",   # Foundation Foods
    "Total lipids (fat)":                  "fat_g",   # SR Legacy
    "Carbohydrate, by difference":         "carbs_g",
    "Fiber, total dietary":                "fiber_g",
    "Total sugars":                        "sugar_g",
    "Sugars, total including NLEA":        "sugar_g", # Foundation Foods
    "Sugars, total":                       "sugar_g", # SR Legacy
    "Sugars, Total":                       "sugar_g", # SR Legacy variant
    "Fatty acids, total saturated":        "saturated_fat_g",
    "Cholesterol":                         "cholesterol_mg",
    "Sodium, Na":                          "sodium_mg",
}

# ── Helpers ─────────────────────────────────────────────────

# Build a lowercase lookup for case-insensitive matching
_NUTRIENT_MAP_LOWER = {k.lower(): v for k, v in NUTRIENT_MAP.items()}


def extract_nutrients(food_nutrients: list) -> dict:
    """
    Pull core nutrients out of a foodNutrients array.
    Returns a dict like {"kcal": 357.0, "protein_g": 12.1, ...}
    Matching is case-insensitive to handle USDA dataset inconsistencies.
    """
    result = {}
    for fn in food_nutrients:
        nutrient_name = fn.get("nutrient", {}).get("name", "").lower()
        if nutrient_name in _NUTRIENT_MAP_LOWER:
            amount = fn.get("amount")
            if amount is not None:
                key = _NUTRIENT_MAP_LOWER[nutrient_name]
                result[key] = float(amount)
    return result


def load_foods(path: Path, source_key: str) -> list[dict]:
    """
    Parse a USDA JSON file and return a flat list of food dicts.
    Handles both Foundation Foods and SR Legacy file structures.
    """
    print(f"Loading {path.name} ...", flush=True)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # Foundation Foods: {"FoundationFoods": [...]}
    # SR Legacy:        {"SRLegacyFoods": [...]}
    foods_list = (
        data.get("FoundationFoods")
        or data.get("SRLegacyFoods")
        or []
    )

    results = []
    for food in foods_list:
        description = food.get("description", "").strip()
        if not description:
            continue

        fdcId = str(food.get("fdcId", ""))
        nutrients = extract_nutrients(food.get("foodNutrients", []))

        # Skip entries with no nutritional data at all
        if not nutrients:
            continue

        results.append({
            "description": description,
            "source": source_key,
            "source_id": fdcId,
            "per_100g": nutrients,
        })

    print(f"  → {len(results)} foods with nutritional data", flush=True)
    return results


# ── Main ────────────────────────────────────────────────────

def main():
    # Read DATABASE_URL from environment (same as the app)
    import os
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable not set", file=sys.stderr)
        sys.exit(1)

    # Alembic/app uses asyncpg; we use psycopg2 for this sync script
    sync_url = database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")

    engine = create_engine(sync_url, echo=False)

    all_foods: list[dict] = []
    for source_key, path in FILES:
        if not path.exists():
            print(f"WARNING: {path} not found, skipping", file=sys.stderr)
            continue
        all_foods.extend(load_foods(path, source_key))

    if not all_foods:
        print("No foods loaded. Check file paths.", file=sys.stderr)
        sys.exit(1)

    print(f"\nTotal foods to import: {len(all_foods)}")
    print("Writing to database ...", flush=True)

    inserted = 0
    updated = 0

    import json as _json
    import psycopg2.extras

    # Use raw psycopg2 connection to handle jsonb correctly
    with engine.connect() as conn:
        raw = conn.connection.connection  # unwrap to psycopg2 connection
        with raw.cursor() as cur:
            for i, food in enumerate(all_foods, 1):
                per_100g_json = _json.dumps(food["per_100g"])

                cur.execute(
                    "SELECT id FROM food_items WHERE source = %s AND source_id = %s",
                    (food["source"], food["source_id"]),
                )
                existing = cur.fetchone()

                if existing:
                    cur.execute(
                        """UPDATE food_items
                           SET name = %s, per_100g = %s::jsonb, updated_at = now()
                           WHERE id = %s""",
                        (food["description"], per_100g_json, existing[0]),
                    )
                    updated += 1
                else:
                    cur.execute(
                        """INSERT INTO food_items (id, name, source, source_id, per_100g, updated_at)
                           VALUES (%s, %s, %s, %s, %s::jsonb, now())""",
                        (str(uuid.uuid4()), food["description"],
                         food["source"], food["source_id"], per_100g_json),
                    )
                    inserted += 1

                if i % 500 == 0:
                    raw.commit()
                    print(f"  {i}/{len(all_foods)} ...", flush=True)

            raw.commit()

    print(f"\nDone! Inserted: {inserted}, Updated: {updated}")


if __name__ == "__main__":
    main()
