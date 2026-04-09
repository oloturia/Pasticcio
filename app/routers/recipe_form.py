# ============================================================
# app/routers/recipe_form.py — HTML form for creating recipes
# ============================================================
#
# Routes:
#   GET  /recipes/new  → render the creation form
#   POST /recipes/new  → validate, save recipe + photos, redirect to detail
#
# This router handles only the browser form flow.
# The REST API for programmatic creation is in app/routers/recipes.py.
#
# File uploads:
#   - cover_photo  → saved as a RecipePhoto with is_cover=True
#   - step_photo[] → one file per step, saved as RecipeStepPhoto rows
#     The index in the step_photo[] array matches the position of the
#     corresponding step_text[] entry (same order in the HTML form).

import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import RedirectResponse
from slugify import slugify
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.recipe import (
    Difficulty,
    IngredientUnit,
    Recipe,
    RecipeIngredient,
    RecipePhoto,
    RecipeStatus,
    RecipeStepPhoto,
    RecipeTranslation,
    TranslationStatus,
)
from app.models.user import User
from app.tasks.delivery import deliver_to_followers
from app.templates_env import templates

from app.routers.recipe_utils import save_upload as _save_upload
from app.routers.recipe_utils import unit_options_html as _unit_options_html

router = APIRouter(tags=["frontend"])

# Allowed image MIME types and their extensions
ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png",
                 "image/webp": ".webp", "image/gif": ".gif"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# ============================================================
# Unit options — rendered once, reused in JS for dynamic rows
# ============================================================

# Human-readable labels for ingredient units
_UNIT_LABELS: list[tuple[str, str]] = [
    ("",        "—"),
    ("g",       "g"),
    ("kg",      "kg"),
    ("oz",      "oz"),
    ("lb",      "lb"),
    ("ml",      "ml"),
    ("l",       "l"),
    ("tsp",     "tsp"),
    ("tbsp",    "tbsp"),
    ("cup",     "cup"),
    ("fl_oz",   "fl oz"),
    ("piece",   "piece"),
    ("pinch",   "pinch"),
    ("to_taste","to taste"),
]

# ============================================================
# GET /recipes/new
# ============================================================

@router.get("/recipes/new")
async def new_recipe_page(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Render the recipe creation form. Requires login."""
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    return templates.TemplateResponse("recipe_form.html", {
        "request": request,
        "current_user": current_user,
        "error": None,
        "form": {},
        "unit_options_html": _unit_options_html(),
    })


# ============================================================
# POST /recipes/new
# ============================================================

@router.post("/recipes/new")
async def create_recipe_submit(
    request: Request,
    # --- Basic fields ---
    title: str = Form(...),
    description: str = Form(default=""),
    language: str = Form(default="en"),
    difficulty: str = Form(default=""),
    # --- Timings ---
    prep_time: str = Form(default=""),
    cook_time: str = Form(default=""),
    servings: str = Form(default=""),
    # --- Publish flag ---
    publish: str = Form(default="0"),
    # --- Dietary tags (multiple checkboxes → list) ---
    dietary_tags: list[str] = Form(default=[]),
    # --- Cover photo ---
    cover_photo: UploadFile = File(default=None),
    # --- Dynamic lists (repeated fields) ---
    # Ingredients
    ing_qty: list[str] = Form(default=[], alias="ing_qty[]"),
    ing_unit: list[str] = Form(default=[], alias="ing_unit[]"),
    ing_name: list[str] = Form(default=[], alias="ing_name[]"),
    ing_notes: list[str] = Form(default=[], alias="ing_notes[]"),
    # Steps
    step_text: list[str] = Form(default=[], alias="step_text[]"),
    step_photo: list[UploadFile] = File(default=[], alias="step_photo[]"),
    # --- DB + auth ---
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Validate and save a new recipe submitted via the browser form.

    On success: redirect to the recipe detail page.
    On error: re-render the form with the error message and pre-filled values.
    """
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    # --- Basic validation ---
    title = title.strip()
    if not title:
        return _form_error(request, current_user, "Title is required.", {
            "title": title, "description": description,
        })

    # Filter out completely empty ingredient rows
    ingredients = []
    for i, name in enumerate(ing_name):
        name = name.strip()
        if not name:
            continue
        ingredients.append({
            "sort_order": i + 1,
            "qty": ing_qty[i].strip() if i < len(ing_qty) else "",
            "unit": ing_unit[i] if i < len(ing_unit) else "",
            "name": name,
            "notes": ing_notes[i].strip() if i < len(ing_notes) else "",
        })

    # Filter out empty steps
    steps = [t.strip() for t in step_text if t.strip()]
    if not steps:
        return _form_error(request, current_user, "At least one step is required.", {
            "title": title, "description": description,
        })

    # --- Convert optional numeric fields ---
    prep_seconds = int(prep_time) * 60 if prep_time.strip().isdigit() else None
    cook_seconds = int(cook_time) * 60 if cook_time.strip().isdigit() else None
    servings_int = int(servings) if servings.strip().isdigit() else None
    difficulty_val = Difficulty(difficulty) if difficulty in ("easy", "medium", "hard") else None
    is_published = publish == "1"

    # --- Build unique slug ---
    base_slug = slugify(title)
    slug = base_slug
    existing = await db.execute(
        select(Recipe).where(
            and_(Recipe.author_id == current_user.id, Recipe.slug == slug)
        )
    )
    if existing.scalar_one_or_none():
        slug = f"{base_slug}-{str(uuid.uuid4())[:8]}"

    recipe_id = uuid.uuid4()
    ap_id = f"https://{settings.instance_domain}/users/{current_user.username}/recipes/{slug}"

    # --- Create recipe row ---
    recipe = Recipe(
        id=recipe_id,
        author_id=current_user.id,
        slug=slug,
        ap_id=ap_id,
        original_language=language,
        status=RecipeStatus.PUBLISHED if is_published else RecipeStatus.DRAFT,
        published_at=datetime.now(timezone.utc) if is_published else None,
        dietary_tags=[t.lower() for t in dietary_tags],
        metabolic_tags=[],
        show_metabolic_disclaimer=False,
        prep_time_seconds=prep_seconds,
        cook_time_seconds=cook_seconds,
        servings=servings_int,
        difficulty=difficulty_val,
    )
    db.add(recipe)
    await db.flush()

    # --- Translation row ---
    step_dicts = [{"order": i + 1, "text": text} for i, text in enumerate(steps)]
    translation = RecipeTranslation(
        recipe_id=recipe_id,
        language=language,
        title=title,
        description=description.strip() or None,
        steps=step_dicts,
        status=TranslationStatus.ORIGINAL,
    )
    db.add(translation)

    # --- Ingredient rows ---
    for ing in ingredients:
        try:
            unit_val = ing["unit"] if ing["unit"] else ""
        except ValueError:
            unit_val = IngredientUnit.NONE
        try:
            qty_val = float(ing["qty"]) if ing["qty"] else None
        except ValueError:
            qty_val = None
        db.add(RecipeIngredient(
            recipe_id=recipe_id,
            sort_order=ing["sort_order"],
            quantity=qty_val,
            unit=unit_val,
            name=ing["name"],
            notes=ing["notes"] or None,
        ))

    await db.flush()

    # --- Cover photo ---
    if cover_photo and cover_photo.filename:
        relative_path = await _save_upload(
            cover_photo, f"recipes/{recipe_id}"
        )
        if relative_path:
            db.add(RecipePhoto(
                recipe_id=recipe_id,
                url=relative_path,
                is_cover=True,
                alt_text=title,
            ))

    # --- Step photos ---
    # step_photo[] is aligned with step_text[]: index 0 = step 1, etc.
    # Empty UploadFile objects (no file selected) are skipped.
    for idx, photo_file in enumerate(step_photo):
        if not photo_file or not photo_file.filename:
            continue
        step_num = idx + 1
        # Only save if this step_num actually exists in our steps list
        if step_num > len(steps):
            break
        relative_path = await _save_upload(
            photo_file, f"recipes/{recipe_id}/steps"
        )
        if relative_path:
            db.add(RecipeStepPhoto(
                recipe_id=recipe_id,
                step_order=step_num,
                url=relative_path,
                alt_text=f"{title} — step {step_num}",
            ))

    await db.flush()

    # --- Trigger AP delivery if published ---
    if is_published:
        try:
            deliver_to_followers.delay(str(recipe_id), "create")
        except Exception:
            pass  # delivery failure must never block the response

    return RedirectResponse(f"/api/v1/recipes/{recipe_id}", status_code=302)


# ============================================================
# Helper
# ============================================================

def _form_error(request, current_user, error, form_data):
    """Re-render the form with an error message and pre-filled values."""
    return templates.TemplateResponse("recipe_form.html", {
        "request": request,
        "current_user": current_user,
        "error": error,
        "form": form_data,
        "unit_options_html": _unit_options_html(),
    }, status_code=422)
