# ============================================================
# app/routers/bookmark_form.py — bookmark actions via HTML forms
# ============================================================
#
# The existing /api/v1/bookmarks endpoints use JSON bodies and
# the DELETE method — neither works from a plain HTML form.
# These two routes accept form POSTs from the browser.
#
# Routes:
#   POST /api/v1/bookmarks/add          → add bookmark, redirect back
#   POST /api/v1/bookmarks/{id}/delete  → remove bookmark, redirect back

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.moderation import Bookmark
from app.models.user import User

router = APIRouter(tags=["frontend"])


def _back(request: Request) -> str:
    """Return the Referer URL, falling back to homepage."""
    return request.headers.get("referer", "/")


@router.post("/api/v1/bookmarks/add")
async def add_bookmark_form(
    request: Request,
    recipe_ap_id: str = Form(...),
    title: str = Form(default=""),
    author_ap_id: str = Form(default=""),
    author_name: str = Form(default=""),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Add a bookmark via HTML form. Redirects back to the recipe page."""
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    # Idempotent — ignore if already bookmarked
    existing = await db.execute(
        select(Bookmark).where(
            Bookmark.user_id == current_user.id,
            Bookmark.recipe_ap_id == recipe_ap_id,
        )
    )
    if not existing.scalar_one_or_none():
        db.add(Bookmark(
            user_id=current_user.id,
            recipe_ap_id=recipe_ap_id,
            title=title or None,
            author_ap_id=author_ap_id or None,
            author_name=author_name or None,
        ))
        await db.flush()

    return RedirectResponse(_back(request), status_code=302)


@router.post("/api/v1/bookmarks/{bookmark_id}/delete")
async def remove_bookmark_form(
    bookmark_id: uuid.UUID,
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Remove a bookmark via HTML form. Redirects back to the recipe page."""
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    result = await db.execute(
        select(Bookmark).where(
            Bookmark.id == bookmark_id,
            Bookmark.user_id == current_user.id,
        )
    )
    bm = result.scalar_one_or_none()
    if bm:
        await db.delete(bm)
        await db.flush()

    return RedirectResponse(_back(request), status_code=302)
