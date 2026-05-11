# ============================================================
# app/routers/photos.py — recipe photo upload endpoints
# ============================================================
#
# Endpoints:
#   POST   /api/v1/recipes/{id}/photos         — upload a photo
#   GET    /api/v1/recipes/{id}/photos         — list photos
#   PUT    /api/v1/recipes/{id}/photos/{pid}   — update is_cover / alt_text
#   DELETE /api/v1/recipes/{id}/photos/{pid}   — delete a photo
#
# Files are stored on the local filesystem under MEDIA_ROOT/recipes/{recipe_id}/
# The stored path is relative to MEDIA_ROOT so the domain can change freely.
# The public URL is built as https://{INSTANCE_DOMAIN}/media/{relative_path}.

import os
import uuid
from datetime import datetime
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.recipe import Recipe, RecipePhoto, RecipeStatus
from app.models.user import User
from app.routers.auth import get_current_user

router = APIRouter(tags=["photos"])

# Maximum upload size: 10 MB
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Allowed MIME types
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# Extension map
EXTENSION_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


# ============================================================
# Pydantic schemas
# ============================================================

class PhotoOut(BaseModel):
    id: uuid.UUID
    recipe_id: uuid.UUID
    url: str          # absolute public URL
    alt_text: str | None
    is_cover: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class PhotoUpdateIn(BaseModel):
    is_cover: bool | None = None
    alt_text: str | None = None


# ============================================================
# Helpers
# ============================================================

def _public_url(relative_path: str) -> str:
    """Build the absolute public URL for a stored media file."""
    return f"https://{settings.instance_domain}/media/{relative_path}"


def _photo_to_out(photo: RecipePhoto) -> PhotoOut:
    """Convert a RecipePhoto ORM object to PhotoOut schema."""
    return PhotoOut(
        id=photo.id,
        recipe_id=photo.recipe_id,
        url=_public_url(photo.url),
        alt_text=photo.alt_text,
        is_cover=photo.is_cover,
        created_at=photo.created_at,
    )


async def _get_recipe_for_owner(
    recipe_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> Recipe:
    """
    Load a recipe and verify the current user is the owner.
    Raises 404 if not found, 403 if not the owner.
    """
    result = await db.execute(
        select(Recipe).where(Recipe.id == recipe_id)
    )
    recipe = result.scalar_one_or_none()
    if not recipe or recipe.status == RecipeStatus.DELETED:
        raise HTTPException(status_code=404, detail="Recipe not found")
    if recipe.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return recipe


# ============================================================
# Endpoints
# ============================================================

@router.post(
    "/api/v1/recipes/{recipe_id}/photos",
    response_model=PhotoOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_photo(
    recipe_id: uuid.UUID,
    file: UploadFile = File(...),
    alt_text: str | None = Form(default=None),
    is_cover: bool = Form(default=False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a photo for a recipe. Only the recipe owner can upload.
    Accepts JPEG, PNG, WebP, GIF up to 10 MB.
    If is_cover=True, any existing cover photo is demoted.
    """
    recipe = await _get_recipe_for_owner(recipe_id, current_user, db)

    # Validate content type
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. "
                   f"Allowed: {', '.join(ALLOWED_CONTENT_TYPES)}",
        )

    # Read and check size
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_UPLOAD_BYTES // 1024 // 1024} MB.",
        )

    # Build storage path: recipes/{recipe_id}/{photo_id}.ext
    photo_id = uuid.uuid4()
    ext = EXTENSION_MAP[file.content_type]
    relative_path = f"recipes/{recipe_id}/{photo_id}{ext}"
    absolute_path = Path(settings.media_root) / relative_path

    # Create directory if it does not exist
    absolute_path.parent.mkdir(parents=True, exist_ok=True)

    # Write file to disk asynchronously
    async with aiofiles.open(absolute_path, "wb") as f_out:
        await f_out.write(content)

    # If this photo should be the cover, demote all existing covers first
    if is_cover:
        await db.execute(
            update(RecipePhoto)
            .where(RecipePhoto.recipe_id == recipe_id, RecipePhoto.is_cover.is_(True))
            .values(is_cover=False)
        )

    photo = RecipePhoto(
        id=photo_id,
        recipe_id=recipe_id,
        url=relative_path,
        alt_text=alt_text,
        is_cover=is_cover,
    )
    db.add(photo)
    await db.flush()

    return _photo_to_out(photo)


@router.get(
    "/api/v1/recipes/{recipe_id}/photos",
    response_model=list[PhotoOut],
)
async def list_photos(
    recipe_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """List all photos for a recipe. Cover photo is always first."""
    result = await db.execute(
        select(Recipe).where(Recipe.id == recipe_id)
    )
    recipe = result.scalar_one_or_none()
    if not recipe or recipe.status == RecipeStatus.DELETED:
        raise HTTPException(status_code=404, detail="Recipe not found")

    photos_result = await db.execute(
        select(RecipePhoto)
        .where(RecipePhoto.recipe_id == recipe_id)
        # Cover first, then by creation date
        .order_by(RecipePhoto.is_cover.desc(), RecipePhoto.created_at.asc())
    )
    photos = photos_result.scalars().all()
    return [_photo_to_out(p) for p in photos]


@router.put(
    "/api/v1/recipes/{recipe_id}/photos/{photo_id}",
    response_model=PhotoOut,
)
async def update_photo(
    recipe_id: uuid.UUID,
    photo_id: uuid.UUID,
    data: PhotoUpdateIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update alt_text or is_cover for a photo.
    Only the recipe owner can update photos.
    Setting is_cover=True demotes all other cover photos.
    """
    await _get_recipe_for_owner(recipe_id, current_user, db)

    result = await db.execute(
        select(RecipePhoto).where(
            RecipePhoto.id == photo_id,
            RecipePhoto.recipe_id == recipe_id,
        )
    )
    photo = result.scalar_one_or_none()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    if data.alt_text is not None:
        photo.alt_text = data.alt_text

    if data.is_cover is True:
        # Demote existing cover
        await db.execute(
            update(RecipePhoto)
            .where(
                RecipePhoto.recipe_id == recipe_id,
                RecipePhoto.is_cover.is_(True),
                RecipePhoto.id != photo_id,
            )
            .values(is_cover=False)
        )
        photo.is_cover = True
    elif data.is_cover is False:
        photo.is_cover = False

    await db.flush()
    return _photo_to_out(photo)


@router.delete(
    "/api/v1/recipes/{recipe_id}/photos/{photo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_photo(
    recipe_id: uuid.UUID,
    photo_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a photo. Only the recipe owner can delete photos.
    The file is removed from disk as well.
    """
    await _get_recipe_for_owner(recipe_id, current_user, db)

    result = await db.execute(
        select(RecipePhoto).where(
            RecipePhoto.id == photo_id,
            RecipePhoto.recipe_id == recipe_id,
        )
    )
    photo = result.scalar_one_or_none()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    # Remove file from disk (ignore errors if file is missing)
    absolute_path = Path(settings.media_root) / photo.url
    try:
        absolute_path.unlink(missing_ok=True)
    except OSError:
        pass

    await db.delete(photo)
    await db.flush()
