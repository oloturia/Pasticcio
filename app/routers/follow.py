# ============================================================
# app/routers/follow.py — follow, unfollow, accept/reject requests
# ============================================================
#
# Routes:
#   POST /users/{username}/follow          → send a follow request
#   POST /users/{username}/unfollow        → unfollow a user
#   GET  /follow-requests                  → list pending follow requests
#   POST /follow-requests/{id}/accept      → accept a follow request
#   POST /follow-requests/{id}/reject      → reject a follow request
#
# For LOCAL targets: creates a FollowRequest with status=pending.
#   The target user sees it in their follow requests page and decides.
#   On accept: creates a Follower row and delivers Accept{Follow} AP.
#
# For REMOTE targets: sends a Follow AP activity directly to the remote
#   inbox and creates a pending FollowRequest. When the remote server
#   replies with Accept, the activitypub inbox handler creates the
#   Follower row and updates the request status.

import json
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ap.builder import build_accept_activity
from app.ap.signatures import sign_request
from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.follow_request import FollowRequest, FollowRequestStatus
from app.models.follower import Follower
from app.models.user import User
from app.templates_env import templates
from app.routers.dashboard import create_notification
from app.models.notification import NotificationType

import logging
logger = logging.getLogger(__name__)

router = APIRouter(tags=["frontend"])

AP_CONTENT_TYPE = "application/activity+json"


# ============================================================
# Helpers
# ============================================================

async def _fetch_remote_actor(actor_url: str) -> dict | None:
    """Fetch a remote AP actor. Returns None on failure."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                actor_url,
                headers={"Accept": AP_CONTENT_TYPE},
                follow_redirects=True,
            )
            if resp.status_code == 200:
                return resp.json()
    except httpx.RequestError as exc:
        logger.warning("Failed to fetch actor %s: %s", actor_url, exc)
    return None


async def _deliver_ap(inbox_url: str, activity: dict, sender: User) -> None:
    """Sign and POST an AP activity to a remote inbox."""
    body = json.dumps(activity).encode("utf-8")
    key_id = f"{sender.ap_id}#main-key"
    headers = sign_request(
        method="post",
        url=inbox_url,
        body=body,
        private_key_pem=sender.private_key,
        key_id=key_id,
    )
    headers["Content-Type"] = AP_CONTENT_TYPE
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(inbox_url, content=body, headers=headers)
    except httpx.RequestError as exc:
        logger.warning("Delivery failed to %s: %s", inbox_url, exc)


def _build_follow_activity(follower_ap_id: str, followee_ap_id: str) -> dict:
    """Build a Follow AP activity."""
    return {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Follow",
        "id": f"{follower_ap_id}#follow-{uuid.uuid4()}",
        "actor": follower_ap_id,
        "object": followee_ap_id,
    }


def _build_reject_activity(actor_url: str, follow_activity: dict) -> dict:
    """Build a Reject{Follow} AP activity."""
    return {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Reject",
        "id": f"{actor_url}#reject-{uuid.uuid4()}",
        "actor": actor_url,
        "object": follow_activity,
    }


# ============================================================
# POST /users/{username}/follow
# ============================================================

@router.post("/users/{username}/follow")
async def follow_user(
    username: str,
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Send a follow request to a local user.

    Creates a FollowRequest with status=pending.
    The target user must accept it manually.
    Redirects back to the user's profile on completion.
    """
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    profile_url = f"/users/{username}"

    # Load the target user
    result = await db.execute(
        select(User).where(
            User.username == username,
            User.is_remote.is_(False),
            User.is_active.is_(True),
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Cannot follow yourself
    if target.id == current_user.id:
        return RedirectResponse(profile_url, status_code=302)

    # Check if already following
    existing_follower = await db.execute(
        select(Follower).where(
            Follower.followee_id == target.id,
            Follower.follower_ap_id == current_user.ap_id,
        )
    )
    if existing_follower.scalar_one_or_none():
        return RedirectResponse(profile_url, status_code=302)

    # Check if a pending request already exists
    existing_req = await db.execute(
        select(FollowRequest).where(
            FollowRequest.followee_id == target.id,
            FollowRequest.actor_ap_id == current_user.ap_id,
            FollowRequest.status == FollowRequestStatus.PENDING,
        )
    )
    if existing_req.scalar_one_or_none():
        return RedirectResponse(profile_url, status_code=302)

    # Build the Follow activity (stored so the Accept can reference it)
    follow_activity = _build_follow_activity(current_user.ap_id, target.ap_id)
    local_inbox = f"https://{settings.instance_domain}/users/{current_user.username}/inbox"

    follow_req = FollowRequest(
        followee_id=target.id,
        actor_ap_id=current_user.ap_id,
        actor_inbox=local_inbox,
        follow_activity_id=follow_activity["id"],
        is_local=True,
        requester_id=current_user.id,
        status=FollowRequestStatus.PENDING,
    )
    try:
        db.add(follow_req)
        await db.flush()
    except IntegrityError:
        await db.rollback()
    # Notify the target user
    await create_notification(
        db=db,
        recipient_id=target.id,
        notification_type=NotificationType.NEW_FOLLOWER,
        actor_ap_id=current_user.ap_id,
        actor_display_name=current_user.display_name or current_user.username,
        object_id=current_user.ap_id,
        summary="wants to follow you",
    )
    return RedirectResponse(profile_url, status_code=302)


# ============================================================
# POST /users/{username}/unfollow
# ============================================================

@router.post("/users/{username}/unfollow")
async def unfollow_user(
    username: str,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Remove a follow relationship with a local user."""
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    profile_url = f"/users/{username}"

    result = await db.execute(
        select(User).where(User.username == username, User.is_remote.is_(False))
    )
    target = result.scalar_one_or_none()
    if not target:
        return RedirectResponse(profile_url, status_code=302)

    # Remove follower row
    follower_result = await db.execute(
        select(Follower).where(
            Follower.followee_id == target.id,
            Follower.follower_ap_id == current_user.ap_id,
        )
    )
    follower = follower_result.scalar_one_or_none()
    if follower:
        await db.delete(follower)

    # Remove any pending/accepted follow request
    req_result = await db.execute(
        select(FollowRequest).where(
            FollowRequest.followee_id == target.id,
            FollowRequest.actor_ap_id == current_user.ap_id,
        )
    )
    req = req_result.scalar_one_or_none()
    if req:
        await db.delete(req)

    await db.flush()
    return RedirectResponse(profile_url, status_code=302)


# ============================================================
# GET /follow-requests
# ============================================================

@router.get("/follow-requests")
async def follow_requests_page(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Show pending follow requests for the current user."""
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    result = await db.execute(
        select(FollowRequest)
        .where(
            FollowRequest.followee_id == current_user.id,
            FollowRequest.status == FollowRequestStatus.PENDING,
        )
        .order_by(FollowRequest.created_at.desc())
    )
    pending = result.scalars().all()

    # Build display data for each request
    requests_data = []
    for req in pending:
        if req.is_local and req.requester_id:
            # Load local requester info
            user_result = await db.execute(
                select(User).where(User.id == req.requester_id)
            )
            requester = user_result.scalar_one_or_none()
            requests_data.append({
                "id": str(req.id),
                "actor_ap_id": req.actor_ap_id,
                "username": requester.username if requester else None,
                "display_name": requester.display_name if requester else None,
                "profile_url": f"/users/{requester.username}" if requester else req.actor_ap_id,
                "created_at": req.created_at.strftime("%Y-%m-%d"),
                "is_local": True,
            })
        else:
            # Remote actor — use AP ID to derive display info
            handle = req.actor_ap_id.split("/users/")[-1] if "/users/" in req.actor_ap_id else req.actor_ap_id
            domain = req.actor_ap_id.split("/")[2] if req.actor_ap_id.startswith("https://") else ""
            requests_data.append({
                "id": str(req.id),
                "actor_ap_id": req.actor_ap_id,
                "username": handle,
                "display_name": f"@{handle}@{domain}",
                "profile_url": req.actor_ap_id,
                "created_at": req.created_at.strftime("%Y-%m-%d"),
                "is_local": False,
            })

    return templates.TemplateResponse("follow_requests.html", {
        "request": request,
        "current_user": current_user,
        "pending_requests": requests_data,
    })


# ============================================================
# POST /follow-requests/{id}/accept
# ============================================================

@router.post("/follow-requests/{request_id}/accept")
async def accept_follow_request(
    request_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Accept a pending follow request."""
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    result = await db.execute(
        select(FollowRequest).where(
            FollowRequest.id == request_id,
            FollowRequest.followee_id == current_user.id,
            FollowRequest.status == FollowRequestStatus.PENDING,
        )
    )
    req = result.scalar_one_or_none()
    if not req:
        return RedirectResponse("/follow-requests", status_code=302)

    # Create Follower row
    follower = Follower(
        followee_id=current_user.id,
        follower_ap_id=req.actor_ap_id,
        follower_inbox=req.actor_inbox,
    )
    try:
        db.add(follower)
        await db.flush()
    except IntegrityError:
        await db.rollback()

    # Update request status
    req.status = FollowRequestStatus.ACCEPTED
    req.decided_at = datetime.now(timezone.utc)
    await db.flush()

    # Deliver Accept{Follow} AP activity to the requester
    follow_activity = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Follow",
        "id": req.follow_activity_id or f"{req.actor_ap_id}#follow",
        "actor": req.actor_ap_id,
        "object": current_user.ap_id,
    }
    accept = build_accept_activity(current_user.ap_id, follow_activity)
    await _deliver_ap(req.actor_inbox, accept, current_user)

    return RedirectResponse("/follow-requests", status_code=302)


# ============================================================
# POST /follow-requests/{id}/reject
# ============================================================

@router.post("/follow-requests/{request_id}/reject")
async def reject_follow_request(
    request_id: uuid.UUID,
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Reject a pending follow request."""
    if not current_user:
        return RedirectResponse("/login", status_code=302)

    result = await db.execute(
        select(FollowRequest).where(
            FollowRequest.id == request_id,
            FollowRequest.followee_id == current_user.id,
            FollowRequest.status == FollowRequestStatus.PENDING,
        )
    )
    req = result.scalar_one_or_none()
    if not req:
        return RedirectResponse("/follow-requests", status_code=302)

    req.status = FollowRequestStatus.REJECTED
    req.decided_at = datetime.now(timezone.utc)
    await db.flush()

    # Optionally deliver Reject{Follow} to remote actors
    if not req.is_local:
        follow_activity = {
            "@context": "https://www.w3.org/ns/activitystreams",
            "type": "Follow",
            "id": req.follow_activity_id or f"{req.actor_ap_id}#follow",
            "actor": req.actor_ap_id,
            "object": current_user.ap_id,
        }
        reject = _build_reject_activity(current_user.ap_id, follow_activity)
        await _deliver_ap(req.actor_inbox, reject, current_user)

    return RedirectResponse("/follow-requests", status_code=302)
