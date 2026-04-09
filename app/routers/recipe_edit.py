# ============================================================
# app/routers/recipe_edit.py — inline edit and delete via browser
# ============================================================
#
# Routes:
#   POST /api/v1/recipes/{id}/edit   → update recipe, redirect back
#   POST /api/v1/recipes/{id}/delete → soft-delete, redirect to homepage
#
# The edit form covers all fields: title, description, timings,
# servings, difficulty, dietary tags, ingredients, steps with photos,
# and cover photo. It replaces ingredients and steps entirely on save.

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.cooked_this import CookedThis, CookedThisStatus
from app.models.recipe import (
    Recipe, RecipeIngredient, RecipePhoto, RecipeStatus,
    RecipeStepPhoto, RecipeTranslation, TranslationStatus,
)
from app.models.user import User
from app.routers.recipe_utils import save_upload, unit_options_html
from app.tasks.delivery import deliver_to_followers
from app.templates_env import templates

import logging
logger = logging.getLogger(__name__)

router = APIRouter(tags=["frontend"])


def _recipe_url(recipe_id: str) -> str:
    return f"/api/v1/recipes/{recipe_id}"


def _trigger_delivery(recipe_id: uuid.UUID, activity_type: str) -> None:
    try:
        deliver_to_followers.delay(str(recipe_id), activity_type)
    except Exception:
        logger.warning("Could not enqueue delivery for recipe %s", recipe_id)


async def _load_recipe_full(recipe_id: uuid.UUID, db: AsyncSession):
    """Load a recipe with all relationships needed for edit and re-render."""
    result = await db.execute(
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(
            selectinload(Recipe.author),
            selectinload(Recipe.translations),
            selectinload(Recipe.ingredients),
            selectinload(Recipe.photos),
            selectinload(Recipe.step_photos),
            selectinload(Recipe.cooked_this).options(
                selectinload(CookedThis.author),
                selectinload(CookedThis.photos),
                selectinload(CookedThis.replies).options(
                    selectinload(CookedThis.author),
                    selectinload(CookedThis.photos),
                ),
            ),
        )
    )
    return result.scalar_one_or_none()


def _render_detail(request, recipe, current_user, edit_error=None):
    """Render recipe_detail.html with all required context variables."""
    translation = next(
        (t for t in recipe.translations if t.language == recipe.original_language),
        recipe.translations[0] if recipe.translations else None,
    )
    steps = sorted(translation.steps, key=lambda s: s.get("order", 0)) if translation else []
    cover = next((p for p in recipe.photos if p.is_cover), None)
    if cover is None and recipe.photos:
        cover = recipe.photos[0]
    step_photo_map = {p.step_order: f"/media/{p.url}" for p in recipe.step_photos}
    comments = sorted(
        [c for c in recipe.cooked_this
         if c.status == CookedThisStatus.PUBLISHED and c.parent_id is None],
        key=lambda c: c.created_at,
    )
    return templates.TemplateResponse("recipe_detail.html", {
        "request": request,
        "recipe": recipe,
        "translation": translation,
        "ingredients": recipe.ingredients,
        "steps": steps,
        "cover_url": f"/media/{cover.url}" if cover else None,
        "cover_alt": cover.alt_text if cover else None,
        "step_photo_map": step_photo_map,
        "current_user": current_user,
        "comments": comments,
        "comment_error": None,
        "comment_content": None,
        "edit_error": edit_error,
        "unit_options_html": unit_options_html(),
    }, status_code=422 if edit_error else 200)


# ============================================================
# POST /api/v1/recipes/{id}/edit
# ============================================================

@router.post("/api/v1/recipes/{recipe_id}/edit")
async def edit_recipe_submit(
    recipe_id: uuid.UUID,
    request: Request,
    title: str = Form(...),
    description: str = Form(default=""),
    difficulty: str = Form(default=""),
    prep_time: str = Form(default=""),
    cook_time: str = Form(default=""),
    servings: str = Form(default=""),
    publish: str = Form(default="0"),
    dietary_tags: list[str] = Form(default=[]),
    cover_photo: UploadFile = File(default=None),
    ing_qty: list[str] = Form(default=[], alias="ing_qty[]"),
    ing_unit: list[str] = Form(default=[], alias="ing_unit[]"),
    ing_name: list[str] = Form(default=[], alias="ing_name[]"),
    ing_notes: list[str] = Form(default=[], alias="ing_notes[]"),
    step_text: list[str] = Form(default=[], alias="step_text[]"),
    step_photo: list[UploadFile] = File(default=[], alias="step_photo[]"),
    step_keep_photo: list[str] = Form(default=[], alias="step_keep_photo[]"),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Handle the full inline edit form submission."""
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    recipe = await _load_recipe_full(recipe_id, db)
    if not recipe or recipe.status == RecipeStatus.DELETED:
        return RedirectResponse("/", status_code=302)
    if recipe.author_id != current_user.id:
        return RedirectResponse(_recipe_url(str(recipe_id)), status_code=302)

    title = title.strip()
    if not title:
        return _render_detail(request, recipe, current_user, "Title is required.")

    steps = [t.strip() for t in step_text if t.strip()]
    if not steps:
        return _render_detail(request, recipe, current_user, "At least one step is required.")

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

    # Update recipe metadata
    was_published = recipe.status == RecipeStatus.PUBLISHED
    just_published = False

    recipe.dietary_tags = [t.lower() for t in dietary_tags]
    recipe.prep_time_seconds = int(prep_time) * 60 if prep_time.strip().isdigit() else None
    recipe.cook_time_seconds = int(cook_time) * 60 if cook_time.strip().isdigit() else None
    recipe.servings = int(servings) if servings.strip().isdigit() else None
    recipe.difficulty = difficulty if difficulty in ("easy", "medium", "hard") else None

    if publish == "1" and recipe.status == RecipeStatus.DRAFT:
        recipe.status = RecipeStatus.PUBLISHED
        recipe.published_at = datetime.now(timezone.utc)
        just_published = True

    # Update translation
    trans_result = await db.execute(
        select(RecipeTranslation).where(
            and_(
                RecipeTranslation.recipe_id == recipe_id,
                RecipeTranslation.language == recipe.original_language,
            )
        )
    )
    translation = trans_result.scalar_one_or_none()
    step_dicts = [{"order": i + 1, "text": text} for i, text in enumerate(steps)]

    if translation:
        translation.title = title
        translation.description = description.strip() or None
        translation.steps = step_dicts
    else:
        db.add(RecipeTranslation(
            recipe_id=recipe_id,
            language=recipe.original_language,
            title=title,
            description=description.strip() or None,
            steps=step_dicts,
            status=TranslationStatus.ORIGINAL,
        ))

    # Replace ingredients entirely
    for ing in recipe.ingredients:
        await db.delete(ing)
    await db.flush()

    for ing in ingredients:
        try:
            qty_val = float(ing["qty"]) if ing["qty"] else None
        except ValueError:
            qty_val = None
        db.add(RecipeIngredient(
            recipe_id=recipe_id,
            sort_order=ing["sort_order"],
            quantity=qty_val,
            unit=ing["unit"] if ing["unit"] else "",
            name=ing["name"],
            notes=ing["notes"] or None,
        ))

    # Cover photo — replace only if a new file was uploaded
    if cover_photo and cover_photo.filename:
        for p in recipe.photos:
            if p.is_cover:
                await db.delete(p)
        await db.flush()
        relative_path = await save_upload(cover_photo, f"recipes/{recipe_id}")
        if relative_path:
            db.add(RecipePhoto(
                recipe_id=recipe_id,
                url=relative_path,
                is_cover=True,
                alt_text=title,
            ))

    # Step photos — keep existing if no new file uploaded for that step
    existing_step_photos = {p.step_order: p for p in recipe.step_photos}
    for p in recipe.step_photos:
        await db.delete(p)
    await db.flush()

    for idx, step_text_item in enumerate(steps):
        step_num = idx + 1
        new_file = step_photo[idx] if idx < len(step_photo) else None
        keep = idx < len(step_keep_photo) and step_keep_photo[idx] == "keep"

        if new_file and new_file.filename:
            relative_path = await save_upload(new_file, f"recipes/{recipe_id}/steps")
            if relative_path:
                db.add(RecipeStepPhoto(
                    recipe_id=recipe_id,
                    step_order=step_num,
                    url=relative_path,
                    alt_text=f"{title} — step {step_num}",
                ))
        elif keep and step_num in existing_step_photos:
            old = existing_step_photos[step_num]
            db.add(RecipeStepPhoto(
                recipe_id=recipe_id,
                step_order=step_num,
                url=old.url,
                alt_text=old.alt_text,
            ))

    await db.flush()

    if just_published:
        _trigger_delivery(recipe_id, "create")
    elif was_published:
        _trigger_delivery(recipe_id, "update")

    return RedirectResponse(_recipe_url(str(recipe_id)), status_code=302)


# ============================================================
# POST /api/v1/recipes/{id}/delete
# ============================================================

@router.post("/api/v1/recipes/{recipe_id}/delete")
async def delete_recipe_submit(
    recipe_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a recipe. Only the author can delete."""
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    result = await db.execute(select(Recipe).where(Recipe.id == recipe_id))
    recipe = result.scalar_one_or_none()

    if not recipe or recipe.status == RecipeStatus.DELETED:
        return RedirectResponse("/", status_code=302)
    if recipe.author_id != current_user.id:
        return RedirectResponse(_recipe_url(str(recipe_id)), status_code=302)

    was_published = recipe.status == RecipeStatus.PUBLISHED
    recipe.status = RecipeStatus.DELETED
    await db.flush()

    if was_published:
        _trigger_delivery(recipe_id, "delete")

    return RedirectResponse("/", status_code=302)
