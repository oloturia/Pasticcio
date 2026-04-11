# ============================================================
# app/routers/comments.py — CookedThis / comments endpoints
# ============================================================

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.cooked_this import CookedThis, CookedThisStatus
from app.models.notification import NotificationType
from app.models.recipe import Recipe, RecipeStatus, RecipeTranslation  # ← aggiunto RecipeTranslation
from app.models.user import User
from app.routers.auth import get_current_user
from app.routers.dashboard import create_notification
from app.tasks.delivery import deliver_comment_to_followers

import logging
logger = logging.getLogger(__name__)

router = APIRouter(tags=["comments"])


# ============================================================
# Pydantic schemas
# ============================================================

class CommentIn(BaseModel):
    content: str
    parent_id: uuid.UUID | None = None


class CommentModerationIn(BaseModel):
    status: str  # "published" or "rejected"


class CommentOut(BaseModel):
    id: uuid.UUID
    recipe_id: uuid.UUID
    actor_ap_id: str
    ap_id: str | None
    in_reply_to: str | None
    parent_id: uuid.UUID | None
    content: str
    is_remote: bool
    status: str
    created_at: datetime
    replies: list["CommentOut"] = []

    model_config = {"from_attributes": True}


CommentOut.model_rebuild()


# ============================================================
# Helpers
# ============================================================

def _build_comment_ap_id(username: str, comment_id: uuid.UUID) -> str:
    return f"https://{settings.instance_domain}/users/{username}/comments/{comment_id}"


def _load_options():
    """Eager-load replies up to 3 levels deep."""
    return selectinload(CookedThis.replies).selectinload(
        CookedThis.replies
    ).selectinload(CookedThis.replies)


# ============================================================
# Endpoints
# ============================================================

@router.get(
    "/api/v1/recipes/{recipe_id}/comments",
    response_model=list[CommentOut],
)
async def list_comments(
    recipe_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List published top-level comments for a recipe, with nested replies."""
    result = await db.execute(
        select(Recipe).where(Recipe.id == recipe_id)
    )
    recipe = result.scalar_one_or_none()
    if not recipe or recipe.status == RecipeStatus.DELETED:
        raise HTTPException(status_code=404, detail="Recipe not found")

    result = await db.execute(
        select(CookedThis)
        .where(
            CookedThis.recipe_id == recipe_id,
            CookedThis.status == CookedThisStatus.PUBLISHED,
            CookedThis.parent_id.is_(None),
        )
        .options(_load_options())
        .order_by(CookedThis.created_at.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    return result.scalars().all()


@router.post(
    "/api/v1/recipes/{recipe_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_comment(
    recipe_id: uuid.UUID,
    data: CommentIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):  
    """Submit a local comment. Requires authentication."""
    result = await db.execute(
        select(Recipe).where(Recipe.id == recipe_id)
    )
    recipe = result.scalar_one_or_none()
    if not recipe or recipe.status == RecipeStatus.DELETED:
        raise HTTPException(status_code=404, detail="Recipe not found")
    
    # Validate parent if provided
    parent = None
    if data.parent_id:
        parent_result = await db.execute(
            select(CookedThis).where(
                CookedThis.id == data.parent_id,
                CookedThis.recipe_id == recipe_id,
            )
        )
        parent = parent_result.scalar_one_or_none()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent comment not found")

    comment_id = uuid.uuid4()
    actor_url = f"https://{settings.instance_domain}/users/{current_user.username}"
    ap_id = _build_comment_ap_id(current_user.username, comment_id)
    in_reply_to = parent.ap_id if parent else recipe.ap_id

    comment = CookedThis(
        id=comment_id,
        recipe_id=recipe_id,
        author_id=current_user.id,
        actor_ap_id=actor_url,
        ap_id=ap_id,
        in_reply_to=in_reply_to,
        parent_id=data.parent_id,
        content=data.content,
        is_remote=False,
        status=CookedThisStatus.PUBLISHED,
    )
    db.add(comment)
    await db.flush()

    # --- Notify recipe author (skip self-comments) ---

    if recipe.author_id != current_user.id:
        # Get recipe title for the notification summary
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

    # --- Deliver to followers via Celery ---
    try:
        deliver_comment_to_followers.delay(str(comment_id))
    except Exception:
        logger.warning("Could not enqueue comment delivery for %s", comment_id)

    # Reload with replies eager-loaded
    result = await db.execute(
        select(CookedThis)
        .where(CookedThis.id == comment_id)
        .options(_load_options())
    )
    return result.scalar_one()


@router.put(
    "/api/v1/recipes/{recipe_id}/comments/{comment_id}",
    response_model=CommentOut,
)
async def moderate_comment(
    recipe_id: uuid.UUID,
    comment_id: uuid.UUID,
    data: CommentModerationIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve or reject a comment. Only the recipe author or admin can moderate."""
    if data.status not in ("published", "rejected"):
        raise HTTPException(status_code=400, detail="Status must be published or rejected")

    result = await db.execute(
        select(CookedThis)
        .where(CookedThis.id == comment_id, CookedThis.recipe_id == recipe_id)
        .options(
            selectinload(CookedThis.recipe),
            _load_options(),
        )
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if comment.recipe.author_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    comment.status = CookedThisStatus(data.status)
    await db.flush()

    result = await db.execute(
        select(CookedThis)
        .where(CookedThis.id == comment_id)
        .options(_load_options())
    )
    return result.scalar_one()
