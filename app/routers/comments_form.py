# ============================================================
# app/routers/comments_form.py — browser form submit for comments
# ============================================================
#
# Route:
#   POST /api/v1/recipes/{recipe_id}/comments/submit
#
# Handles the HTML form submit from recipe_detail.html.
# On success: redirects back to the recipe page (Post/Redirect/Get pattern).
# On error: redirects back with an error message in the query string.
#
# Supports up to 4 photo attachments per comment.
# Photos are saved to MEDIA_ROOT/comments/{comment_id}/{n}.ext

import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.cooked_this import CookedThis, CookedThisPhoto, CookedThisStatus
from app.models.recipe import Recipe, RecipeStatus
from app.models.user import User
from app.models.recipe import RecipeTranslation
from app.models.notification import NotificationType
from app.routers.dashboard import create_notification
from app.tasks.delivery import deliver_comment_to_followers

import logging
logger = logging.getLogger(__name__)

router = APIRouter(tags=["frontend"])

MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _recipe_url(recipe_id: str) -> str:
    return f"/api/v1/recipes/{recipe_id}"


async def _save_photo(file: UploadFile, comment_id: uuid.UUID, index: int) -> str | None:
    """Save a single uploaded photo. Returns relative path or None on failure."""
    if not file or not file.filename:
        return None
    content_type = file.content_type or ""
    if content_type not in ALLOWED_TYPES:
        return None
    content = await file.read()
    if not content or len(content) > MAX_PHOTO_BYTES:
        return None

    ext = ALLOWED_TYPES[content_type]
    relative_path = f"comments/{comment_id}/{index}{ext}"
    absolute_path = Path(settings.media_root) / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiofiles.open(absolute_path, "wb") as f:
        await f.write(content)

    return relative_path


@router.post("/api/v1/recipes/{recipe_id}/comments/submit")
async def submit_comment(
    recipe_id: uuid.UUID,
    request: Request,
    content: str = Form(...),
    parent_id: str = Form(default=""),
    # Up to 4 photo attachments named photo_0 … photo_3
    photo_0: UploadFile = File(default=None),
    photo_1: UploadFile = File(default=None),
    photo_2: UploadFile = File(default=None),
    photo_3: UploadFile = File(default=None),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Handle comment form submission from the recipe detail page.

    Uses Post/Redirect/Get to prevent duplicate submissions on refresh.
    Saves up to 4 photo attachments per comment.
    """
    recipe_url = _recipe_url(str(recipe_id))

    if not current_user:
        return RedirectResponse("/login", status_code=302)

    result = await db.execute(
        select(Recipe).where(Recipe.id == recipe_id)
    )
    recipe = result.scalar_one_or_none()
    if not recipe or recipe.status == RecipeStatus.DELETED:
        return RedirectResponse("/", status_code=302)

    content = content.strip()
    if not content:
        return RedirectResponse(f"{recipe_url}?comment_error=empty", status_code=302)
    if len(content) > 2000:
        return RedirectResponse(f"{recipe_url}?comment_error=toolong", status_code=302)

    # Resolve optional parent comment
    parent_uuid = None
    parent_comment = None
    if parent_id.strip():
        try:
            parent_uuid = uuid.UUID(parent_id.strip())
            parent_result = await db.execute(
                select(CookedThis).where(
                    CookedThis.id == parent_uuid,
                    CookedThis.recipe_id == recipe_id,
                )
            )
            parent_comment = parent_result.scalar_one_or_none()
            if not parent_comment:
                parent_uuid = None
        except ValueError:
            parent_uuid = None

    comment_id = uuid.uuid4()
    actor_url = f"https://{settings.instance_domain}/users/{current_user.username}"
    ap_id = f"{actor_url}/comments/{comment_id}"
    in_reply_to = parent_comment.ap_id if parent_comment else recipe.ap_id

    comment = CookedThis(
        id=comment_id,
        recipe_id=recipe_id,
        author_id=current_user.id,
        actor_ap_id=actor_url,
        ap_id=ap_id,
        in_reply_to=in_reply_to,
        parent_id=parent_uuid,
        content=content,
        is_remote=False,
        status=CookedThisStatus.PUBLISHED,
    )
    db.add(comment)
    await db.flush()

    # Save up to 4 photos
    for idx, photo_file in enumerate([photo_0, photo_1, photo_2, photo_3]):
        relative_path = await _save_photo(photo_file, comment_id, idx)
        if relative_path:
            db.add(CookedThisPhoto(
                cooked_this_id=comment_id,
                sort_order=idx,
                url=relative_path,
                alt_text=f"Photo by {current_user.username}",
            ))

    await db.flush()

    # Notify recipe author — skip self-comments
    if recipe.author_id != current_user.id:
        title_result = await db.execute(
            select(RecipeTranslation)
            .where(
                RecipeTranslation.recipe_id == recipe_id,
                RecipeTranslation.language == recipe.original_language,
            )
            .limit(1)
        )
        title_trans = title_result.scalar_one_or_none()
        recipe_title = title_trans.title if title_trans else "your recipe"

        await create_notification(
            db=db,
            recipient_id=recipe.author_id,
            notification_type=NotificationType.NEW_COMMENT,
            actor_ap_id=current_user.ap_id,
            actor_display_name=current_user.display_name or current_user.username,
            object_id=str(recipe_id),
            summary=f'commented on "{recipe_title}"',
        )

    try:
        deliver_comment_to_followers.delay(str(comment_id))
    except Exception:
        logger.warning("Could not enqueue comment delivery for %s", comment_id)

    return RedirectResponse(f"{recipe_url}#comments", status_code=302)
