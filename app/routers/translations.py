# ============================================================
# app/routers/translations.py — recipe translation endpoints
# ============================================================
#
# Routes:
#   GET  /recipes/{id}/translate             → show split-screen editor
#   POST /recipes/{id}/translate             → save a translation
#   POST /recipes/{id}/translations/{lang}/review → mark as reviewed
#   DELETE /recipes/{id}/translations/{lang} → delete a translation
#
# A recipe can have many RecipeTranslation rows, one per language.
# The original language has status=original and cannot be deleted.
# Community members can submit translations (status=draft).
# The original author can mark them as reviewed (status=reviewed).

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.recipe import (
    Recipe,
    RecipeStatus,
    RecipeTranslation,
    TranslationStatus,
)
from app.models.user import User
from app.templates_env import templates

router = APIRouter(tags=["translations"])

# Supported languages — BCP-47 code → human-readable name
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "it": "Italiano",
    "fr": "Français",
    "de": "Deutsch",
    "es": "Español",
    "pt": "Português",
    "nl": "Nederlands",
    "pl": "Polski",
    "ru": "Русский",
    "ja": "日本語",
    "zh": "中文",
    "ar": "العربية",
}


# ============================================================
# Helpers
# ============================================================

def _recipe_url(recipe_id: uuid.UUID, lang: str | None = None) -> str:
    """Build the URL for a recipe detail page, optionally with a lang parameter."""
    base = f"/api/v1/recipes/{recipe_id}"
    return f"{base}?lang={lang}" if lang else base


async def _load_recipe(
    recipe_id: uuid.UUID,
    db: AsyncSession,
    require_published: bool = False,
) -> Recipe:
    """Load a recipe with translations. Raises 404 if not found or deleted."""
    result = await db.execute(
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(
            selectinload(Recipe.author),
            selectinload(Recipe.translations),
        )
    )
    recipe = result.scalar_one_or_none()
    if not recipe or recipe.status == RecipeStatus.DELETED:
        raise HTTPException(status_code=404, detail="Recipe not found")
    if require_published and recipe.status != RecipeStatus.PUBLISHED:
        raise HTTPException(status_code=404, detail="Recipe not published")
    return recipe


def _get_translation(recipe: Recipe, lang: str) -> RecipeTranslation | None:
    """Return the translation for a given language code, or None."""
    return next((t for t in recipe.translations if t.language == lang), None)


# ============================================================
# GET /recipes/{id}/translate
# ============================================================

@router.get("/recipes/{recipe_id}/translate")
async def translate_page(
    recipe_id: uuid.UUID,
    request: Request,
    # Source language (defaults to recipe's original language)
    from_lang: str | None = Query(default=None, alias="from"),
    # Target language for the new translation
    to_lang: str | None = Query(default=None, alias="to"),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Show the split-screen translation editor.

    Left panel: source translation (readonly).
    Right panel: target language form (editable).

    If a translation in the target language already exists,
    it is pre-filled in the right panel for editing.
    """
    if not current_user:
        return RedirectResponse(f"/login?next=/recipes/{recipe_id}/translate", status_code=302)

    recipe = await _load_recipe(recipe_id, db)

    # Default source language to the recipe's original language
    source_lang = from_lang or recipe.original_language

    # Get the source translation to display on the left
    source_translation = _get_translation(recipe, source_lang)
    if not source_translation:
        # Fall back to original language if requested source is not available
        source_lang = recipe.original_language
        source_translation = _get_translation(recipe, source_lang)

    if not source_translation:
        raise HTTPException(status_code=404, detail="Source translation not found")

    # Build list of available languages for the "translate to" selector
    # Exclude languages that already have an "original" or "reviewed" translation
    # (those should be edited, not replaced)
    existing_langs = {t.language for t in recipe.translations}

    # Get the existing target translation if present (for pre-filling the form)
    target_translation = _get_translation(recipe, to_lang) if to_lang else None

    # Sort steps by order for display
    source_steps = sorted(source_translation.steps, key=lambda s: s.get("order", 0))
    target_steps = sorted(target_translation.steps, key=lambda s: s.get("order", 0)) if target_translation else []

    return templates.TemplateResponse("translate.html", {
        "request": request,
        "current_user": current_user,
        "recipe": recipe,
        "source_lang": source_lang,
        "source_lang_name": SUPPORTED_LANGUAGES.get(source_lang, source_lang),
        "source_translation": source_translation,
        "source_steps": source_steps,
        "to_lang": to_lang or "",
        "target_translation": target_translation,
        "target_steps": target_steps,
        "supported_languages": SUPPORTED_LANGUAGES,
        "existing_langs": existing_langs,
        "is_author": recipe.author_id == current_user.id,
    })


# ============================================================
# POST /recipes/{id}/translate
# ============================================================

@router.post("/recipes/{recipe_id}/translate")
async def save_translation(
    recipe_id: uuid.UUID,
    request: Request,
    # Target language code
    target_lang: str = Form(...),
    # Translated fields
    title: str = Form(...),
    description: str = Form(default=""),
    # Steps: repeated field, one per step (aligned by index)
    step_text: list[str] = Form(default=[], alias="step_text[]"),
    # Optional: mark as reviewed immediately (only author can do this)
    mark_reviewed: str = Form(default="0"),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Save a translation for a recipe.

    - If no translation exists for target_lang: creates a new one (status=draft).
    - If a translation exists: updates it.
    - Only the original recipe author can mark a translation as reviewed.
    - The original language translation (status=original) can only be edited
      by the recipe author via the main edit form.
    """
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    recipe = await _load_recipe(recipe_id, db)

    # Validate target language
    if target_lang not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {target_lang}")

    # Prevent translating into the same language as the original
    # unless the user is the author (they use the main edit form for that)
    if target_lang == recipe.original_language and recipe.author_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Use the main edit form to update the original language",
        )

    # Build steps list — filter out empty ones
    steps = [
        {"order": i + 1, "text": text.strip()}
        for i, text in enumerate(step_text)
        if text.strip()
    ]

    # Determine status
    is_author = recipe.author_id == current_user.id
    if mark_reviewed == "1" and is_author:
        new_status = TranslationStatus.REVIEWED
    else:
        new_status = TranslationStatus.DRAFT

    # Load existing translation for this language
    trans_result = await db.execute(
        select(RecipeTranslation).where(
            and_(
                RecipeTranslation.recipe_id == recipe_id,
                RecipeTranslation.language == target_lang,
            )
        )
    )
    existing = trans_result.scalar_one_or_none()

    if existing:
        # Update existing translation
        # Never downgrade a "original" status (that's the author's version)
        if existing.status != TranslationStatus.ORIGINAL:
            existing.title = title.strip()
            existing.description = description.strip() or None
            existing.steps = steps
            existing.status = new_status
            existing.translated_by_id = current_user.id
        else:
            # It's the original — only update if the user is the author
            if is_author:
                existing.title = title.strip()
                existing.description = description.strip() or None
                existing.steps = steps
    else:
        # Create new translation
        new_translation = RecipeTranslation(
            recipe_id=recipe_id,
            language=target_lang,
            title=title.strip(),
            description=description.strip() or None,
            steps=steps,
            categories=[],
            status=new_status,
            translated_by_id=current_user.id,
        )
        db.add(new_translation)

    await db.flush()

    # Redirect back to the recipe detail in the new language
    return RedirectResponse(
        f"/api/v1/recipes/{recipe_id}?lang={target_lang}",
        status_code=302,
    )


# ============================================================
# POST /recipes/{id}/translations/{lang}/review
# ============================================================

@router.post("/recipes/{recipe_id}/translations/{lang}/review")
async def mark_translation_reviewed(
    recipe_id: uuid.UUID,
    lang: str,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Mark a translation as reviewed. Only the original recipe author can do this.
    This is a quick action — no form needed, just a POST button.
    """
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    recipe = await _load_recipe(recipe_id, db)

    if recipe.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the author can mark translations as reviewed")

    trans_result = await db.execute(
        select(RecipeTranslation).where(
            and_(
                RecipeTranslation.recipe_id == recipe_id,
                RecipeTranslation.language == lang,
            )
        )
    )
    translation = trans_result.scalar_one_or_none()
    if not translation:
        raise HTTPException(status_code=404, detail="Translation not found")

    if translation.status == TranslationStatus.ORIGINAL:
        raise HTTPException(status_code=400, detail="Cannot change status of original translation")

    translation.status = TranslationStatus.REVIEWED
    await db.flush()

    return RedirectResponse(
        f"/api/v1/recipes/{recipe_id}?lang={lang}",
        status_code=302,
    )


# ============================================================
# DELETE /recipes/{id}/translations/{lang}
# ============================================================

@router.post("/recipes/{recipe_id}/translations/{lang}/delete")
async def delete_translation(
    recipe_id: uuid.UUID,
    lang: str,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a translation. Only the recipe author can delete translations.
    The original language translation cannot be deleted.
    Uses POST (not DELETE) for HTML form compatibility.
    """
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    recipe = await _load_recipe(recipe_id, db)

    if recipe.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the author can delete translations")

    trans_result = await db.execute(
        select(RecipeTranslation).where(
            and_(
                RecipeTranslation.recipe_id == recipe_id,
                RecipeTranslation.language == lang,
            )
        )
    )
    translation = trans_result.scalar_one_or_none()
    if not translation:
        raise HTTPException(status_code=404, detail="Translation not found")

    if translation.status == TranslationStatus.ORIGINAL:
        raise HTTPException(status_code=400, detail="Cannot delete the original translation")

    await db.delete(translation)
    await db.flush()

    return RedirectResponse(
        f"/api/v1/recipes/{recipe_id}",
        status_code=302,
    )
