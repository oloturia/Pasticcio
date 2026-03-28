# ============================================================
# app/routers/moderation.py — blocks, mutes, bookmarks, admin
# ============================================================
#
# User endpoints:
#   POST   /api/v1/users/{ap_id}/block    — block a user
#   POST   /api/v1/users/{ap_id}/mute     — mute a user
#   DELETE /api/v1/users/{ap_id}/block    — unblock
#   DELETE /api/v1/users/{ap_id}/mute     — unmute
#   GET    /api/v1/blocks                 — list your blocks
#   GET    /api/v1/mutes                  — list your mutes
#
# Bookmark endpoints:
#   POST   /api/v1/bookmarks              — add bookmark
#   DELETE /api/v1/bookmarks/{id}         — remove bookmark
#   GET    /api/v1/bookmarks              — list bookmarks
#
# Admin endpoints:
#   GET    /api/v1/admin/instances        — list instance rules
#   POST   /api/v1/admin/instances        — add instance rule
#   DELETE /api/v1/admin/instances/{domain} — remove instance rule
#   POST   /api/v1/admin/users/{id}/ban   — ban a user (is_active=False)
#   POST   /api/v1/admin/users/{id}/unban — unban a user

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.moderation import Bookmark, BlockType, InstanceRule, RuleType, UserBlock
from app.models.user import User
from app.routers.auth import get_current_user

router = APIRouter(tags=["moderation"])


# ============================================================
# Pydantic schemas
# ============================================================

class BlockOut(BaseModel):
    id: uuid.UUID
    blocked_ap_id: str
    block_type: str
    created_at: datetime
    model_config = {"from_attributes": True}


class BookmarkIn(BaseModel):
    recipe_ap_id: str
    title: str | None = None
    author_ap_id: str | None = None
    author_name: str | None = None


class BookmarkOut(BaseModel):
    id: uuid.UUID
    recipe_ap_id: str
    title: str | None
    author_ap_id: str | None
    author_name: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class InstanceRuleIn(BaseModel):
    domain: str
    rule_type: str  # "block" or "allow"
    reason: str | None = None


class InstanceRuleOut(BaseModel):
    domain: str
    rule_type: str
    reason: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


# ============================================================
# Helpers
# ============================================================

def _require_admin(user: User) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin required")


# ============================================================
# Block / Mute endpoints
# ============================================================

@router.post("/api/v1/users/{blocked_ap_id:path}/block", status_code=status.HTTP_201_CREATED)
async def block_user(
    blocked_ap_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Block a user by their AP ID. Silently succeeds if already blocked."""
    if blocked_ap_id == current_user.ap_id:
        raise HTTPException(status_code=400, detail="Cannot block yourself")

    block = UserBlock(
        blocker_id=current_user.id,
        blocked_ap_id=blocked_ap_id,
        block_type=BlockType.BLOCK,
    )
    try:
        db.add(block)
        await db.flush()
    except IntegrityError:
        await db.rollback()
    return {"status": "blocked"}


@router.post("/api/v1/users/{blocked_ap_id:path}/mute", status_code=status.HTTP_201_CREATED)
async def mute_user(
    blocked_ap_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mute a user by their AP ID."""
    if blocked_ap_id == current_user.ap_id:
        raise HTTPException(status_code=400, detail="Cannot mute yourself")

    block = UserBlock(
        blocker_id=current_user.id,
        blocked_ap_id=blocked_ap_id,
        block_type=BlockType.MUTE,
    )
    try:
        db.add(block)
        await db.flush()
    except IntegrityError:
        await db.rollback()
    return {"status": "muted"}


@router.delete("/api/v1/users/{blocked_ap_id:path}/block", status_code=status.HTTP_204_NO_CONTENT)
async def unblock_user(
    blocked_ap_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a block."""
    await db.execute(
        delete(UserBlock).where(
            UserBlock.blocker_id == current_user.id,
            UserBlock.blocked_ap_id == blocked_ap_id,
            UserBlock.block_type == BlockType.BLOCK,
        )
    )
    await db.flush()


@router.delete("/api/v1/users/{blocked_ap_id:path}/mute", status_code=status.HTTP_204_NO_CONTENT)
async def unmute_user(
    blocked_ap_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a mute."""
    await db.execute(
        delete(UserBlock).where(
            UserBlock.blocker_id == current_user.id,
            UserBlock.blocked_ap_id == blocked_ap_id,
            UserBlock.block_type == BlockType.MUTE,
        )
    )
    await db.flush()


@router.get("/api/v1/blocks", response_model=list[BlockOut])
async def list_blocks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all users you have blocked."""
    result = await db.execute(
        select(UserBlock).where(
            UserBlock.blocker_id == current_user.id,
            UserBlock.block_type == BlockType.BLOCK,
        ).order_by(UserBlock.created_at.desc())
    )
    return result.scalars().all()


@router.get("/api/v1/mutes", response_model=list[BlockOut])
async def list_mutes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all users you have muted."""
    result = await db.execute(
        select(UserBlock).where(
            UserBlock.blocker_id == current_user.id,
            UserBlock.block_type == BlockType.MUTE,
        ).order_by(UserBlock.created_at.desc())
    )
    return result.scalars().all()


# ============================================================
# Bookmark endpoints
# ============================================================

@router.post("/api/v1/bookmarks", response_model=BookmarkOut, status_code=status.HTTP_201_CREATED)
async def add_bookmark(
    data: BookmarkIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bookmark a recipe by its AP ID. Includes cached metadata."""
    # Check if already bookmarked
    existing = await db.execute(
        select(Bookmark).where(
            Bookmark.user_id == current_user.id,
            Bookmark.recipe_ap_id == data.recipe_ap_id,
        )
    )
    bookmark = existing.scalar_one_or_none()
    if bookmark:
        return bookmark

    bookmark = Bookmark(
        user_id=current_user.id,
        recipe_ap_id=data.recipe_ap_id,
        title=data.title,
        author_ap_id=data.author_ap_id,
        author_name=data.author_name,
    )
    db.add(bookmark)
    await db.flush()
    return bookmark


@router.delete("/api/v1/bookmarks/{bookmark_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_bookmark(
    bookmark_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a bookmark."""
    result = await db.execute(
        select(Bookmark).where(
            Bookmark.id == bookmark_id,
            Bookmark.user_id == current_user.id,
        )
    )
    bookmark = result.scalar_one_or_none()
    if not bookmark:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    await db.delete(bookmark)
    await db.flush()


@router.get("/api/v1/bookmarks", response_model=list[BookmarkOut])
async def list_bookmarks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all your bookmarks, most recent first."""
    result = await db.execute(
        select(Bookmark)
        .where(Bookmark.user_id == current_user.id)
        .order_by(Bookmark.created_at.desc())
    )
    return result.scalars().all()


# ============================================================
# Admin endpoints
# ============================================================

@router.get("/api/v1/admin/instances", response_model=list[InstanceRuleOut])
async def list_instance_rules(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all instance federation rules. Admin only."""
    _require_admin(current_user)
    result = await db.execute(
        select(InstanceRule).order_by(InstanceRule.created_at.desc())
    )
    return result.scalars().all()


@router.post("/api/v1/admin/instances", response_model=InstanceRuleOut, status_code=status.HTTP_201_CREATED)
async def add_instance_rule(
    data: InstanceRuleIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add or update a federation rule for an instance. Admin only."""
    _require_admin(current_user)

    if data.rule_type not in ("block", "allow"):
        raise HTTPException(status_code=400, detail="rule_type must be 'block' or 'allow'")

    # Upsert — replace if exists
    existing = await db.execute(
        select(InstanceRule).where(InstanceRule.domain == data.domain)
    )
    rule = existing.scalar_one_or_none()

    if rule:
        rule.rule_type = RuleType(data.rule_type)
        rule.reason = data.reason
        rule.created_by_id = current_user.id
    else:
        rule = InstanceRule(
            domain=data.domain,
            rule_type=RuleType(data.rule_type),
            reason=data.reason,
            created_by_id=current_user.id,
        )
        db.add(rule)

    await db.flush()
    return rule


@router.delete("/api/v1/admin/instances/{domain}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_instance_rule(
    domain: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a federation rule for an instance. Admin only."""
    _require_admin(current_user)
    await db.execute(
        delete(InstanceRule).where(InstanceRule.domain == domain)
    )
    await db.flush()


@router.post("/api/v1/admin/users/{user_id}/ban", status_code=status.HTTP_200_OK)
async def ban_user(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ban a local user (set is_active=False). Admin only."""
    _require_admin(current_user)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_admin:
        raise HTTPException(status_code=403, detail="Cannot ban an admin")

    user.is_active = False
    await db.flush()
    return {"status": "banned", "username": user.username}


@router.post("/api/v1/admin/users/{user_id}/unban", status_code=status.HTTP_200_OK)
async def unban_user(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Unban a local user (set is_active=True). Admin only."""
    _require_admin(current_user)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = True
    await db.flush()
    return {"status": "unbanned", "username": user.username}
