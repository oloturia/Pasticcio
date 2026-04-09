# ============================================================
# app/dependencies.py — shared FastAPI dependencies
# ============================================================
#
# get_current_user_optional() reads the session cookie and returns
# the logged-in User, or None if the cookie is missing or invalid.
#
# This is the "soft" version of get_current_user() from auth.py,
# which raises 401 if not authenticated. Use this one for pages
# that are public but want to show different content to logged-in users
# (e.g. the homepage showing a "New recipe" button only if logged in).
#
# Usage in a router:
#   from app.dependencies import get_current_user_optional
#
#   @router.get("/")
#   async def page(
#       request: Request,
#       current_user = Depends(get_current_user_optional),
#   ):
#       return templates.TemplateResponse("page.html", {
#           "request": request,
#           "current_user": current_user,   # None if not logged in
#       })

import uuid

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import decode_access_token
from app.database import get_db
from app.models.user import User

# Name of the session cookie set by frontend_auth.py
SESSION_COOKIE = "session"


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """
    Read the session cookie and return the logged-in User, or None.

    Never raises an exception — always returns either a User or None.
    Safe to use as a dependency on any public page.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None

    user_id_str = decode_access_token(token)
    if not user_id_str:
        return None

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        return None

    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active.is_(True))
    )
    return result.scalar_one_or_none()

    async def get_unread_notification_count(
        current_user: User | None,
        db: AsyncSession,
    ) -> int:
        if not current_user:
            return 0
        result = await db.execute(
            select(func.count()).where(
                Notification.recipient_id == current_user.id,
                Notification.read_at.is_(None),
            )
        )
        return result.scalar() or 0
