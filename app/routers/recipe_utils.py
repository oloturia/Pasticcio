# ============================================================
# app/routers/recipe_utils.py — shared helpers for recipe forms
# ============================================================
#
# Extracted from recipe_form.py so that both recipe_form.py
# (create) and recipe_edit.py (edit) can import them without
# duplicating code.

import uuid
from pathlib import Path

import aiofiles
from fastapi import UploadFile

from app.config import settings

ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png",
                 "image/webp": ".webp", "image/gif": ".gif"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

_UNIT_LABELS: list[tuple[str, str]] = [
    ("",         "—"),
    ("g",        "g"),
    ("kg",       "kg"),
    ("oz",       "oz"),
    ("lb",       "lb"),
    ("ml",       "ml"),
    ("l",        "l"),
    ("tsp",      "tsp"),
    ("tbsp",     "tbsp"),
    ("cup",      "cup"),
    ("fl_oz",    "fl oz"),
    ("piece",    "piece"),
    ("pinch",    "pinch"),
    ("to_taste", "to taste"),
]


def unit_options_html(selected: str = "") -> str:
    """Build the <option> HTML string for a unit <select>."""
    parts = []
    for value, label in _UNIT_LABELS:
        sel = " selected" if value == selected else ""
        parts.append(f'<option value="{value}"{sel}>{label}</option>')
    return "".join(parts)


async def save_upload(file: UploadFile, relative_dir: str) -> str | None:
    """
    Validate and save an uploaded image file.
    Returns the path relative to MEDIA_ROOT on success, None otherwise.
    """
    if not file or not file.filename:
        return None
    content_type = file.content_type or ""
    if content_type not in ALLOWED_TYPES:
        return None
    content = await file.read()
    if not content or len(content) > MAX_UPLOAD_BYTES:
        return None

    ext = ALLOWED_TYPES[content_type]
    filename = f"{uuid.uuid4()}{ext}"
    relative_path = f"{relative_dir}/{filename}"
    absolute_path = Path(settings.media_root) / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiofiles.open(absolute_path, "wb") as f:
        await f.write(content)

    return relative_path
