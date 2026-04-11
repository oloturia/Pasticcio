# ============================================================
# tests/test_follow_notifications.py
# ============================================================

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.follow_request import FollowRequest, FollowRequestStatus
from app.models.follower import Follower
from app.models.notification import Notification, NotificationType
from app.models.user import User

# URL del database di test — stessa usata da conftest.py
from app.config import settings
TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/pasticcio_dev_test"


async def _fresh_session() -> AsyncSession:
    """Create a brand-new DB session to read committed data."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return Session(), engine


# ============================================================
# Helpers
# ============================================================

RECIPE_PAYLOAD = {
    "translation": {
        "language": "en",
        "title": "Follow Test Recipe",
        "description": "A recipe for follow tests.",
        "steps": [],
    },
    "original_language": "en",
    "ingredients": [],
    "publish": True,
}

FAKE_REMOTE_ACTOR = {
    "type": "Person",
    "id": "https://remote.example.com/users/remoteuser",
    "inbox": "https://remote.example.com/users/remoteuser/inbox",
    "publicKey": {
        "id": "https://remote.example.com/users/remoteuser#main-key",
        "owner": "https://remote.example.com/users/remoteuser",
        "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----",
    },
}


async def _register_and_login(client, username, email, password="TestPass123!"):
    """Register and return Bearer headers for API calls."""
    await client.post("/api/v1/auth/register", json={
        "username": username, "email": email,
        "password": password, "display_name": username.capitalize(),
    })
    r = await client.post("/api/v1/auth/login", data={
        "username": username, "password": password,
    })
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_cookie_headers(bearer_headers: dict) -> dict:
    """Convert Bearer headers to session cookie headers for HTML routes."""
    token = bearer_headers["Authorization"].split("Bearer ")[1]
    return {"Cookie": f"session={token}"}


async def _create_recipe(client, auth_headers, publish=True, title="Follow Test Recipe"):
    payload = {**RECIPE_PAYLOAD, "publish": publish}
    payload["translation"] = {**RECIPE_PAYLOAD["translation"], "title": title}
    r = await client.post("/api/v1/recipes/", json=payload, headers=auth_headers)
    assert r.status_code == 201
    return r.json()


async def _post_to_inbox(client, username, activity):
    with (
        patch("app.routers.activitypub.verify_request", return_value=True),
        patch("app.routers.activitypub._fetch_remote_actor",
              new=AsyncMock(return_value=FAKE_REMOTE_ACTOR)),
        patch("app.routers.activitypub._deliver_activity", new=AsyncMock()),
    ):
        return await client.post(
            f"/users/{username}/inbox",
            content=json.dumps(activity),
            headers={"Content-Type": "application/activity+json"},
        )


async def _read_follow_request(req_id) -> FollowRequest | None:
    """Read a FollowRequest from a fresh DB session (sees committed data)."""
    session, engine = await _fresh_session()
    try:
        result = await session.execute(
            select(FollowRequest).where(FollowRequest.id == req_id)
        )
        return result.scalar_one_or_none()
    finally:
        await session.close()
        await engine.dispose()


async def _count_follow_requests(**kwargs) -> int:
    """Count FollowRequests matching kwargs from a fresh DB session."""
    session, engine = await _fresh_session()
    try:
        q = select(FollowRequest)
        if "status" in kwargs:
            q = q.where(FollowRequest.status == kwargs["status"])
        result = await session.execute(q)
        return len(result.scalars().all())
    finally:
        await session.close()
        await engine.dispose()


async def _count_followers() -> int:
    session, engine = await _fresh_session()
    try:
        result = await session.execute(select(Follower))
        return len(result.scalars().all())
    finally:
        await session.close()
        await engine.dispose()


async def _read_notification(notif_id) -> Notification | None:
    session, engine = await _fresh_session()
    try:
        result = await session.execute(
            select(Notification).where(Notification.id == notif_id)
        )
        return result.scalar_one_or_none()
    finally:
        await session.close()
        await engine.dispose()


# ============================================================
# Follow — send request
# ============================================================

async def test_follow_creates_pending_request(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Clicking Follow creates a FollowRequest with status=pending."""
    await _register_and_login(client, "otherone", "otherone@example.com")
    cookie_h = _make_cookie_headers(auth_headers)

    r = await client.post("/users/otherone/follow", headers=cookie_h, follow_redirects=False)
    assert r.status_code == 302

    count = await _count_follow_requests(status=FollowRequestStatus.PENDING)
    assert count == 1


async def test_follow_self_does_nothing(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Cannot follow yourself."""
    cookie_h = _make_cookie_headers(auth_headers)
    await client.post("/users/testuser/follow", headers=cookie_h, follow_redirects=False)

    count = await _count_follow_requests()
    assert count == 0


async def test_follow_duplicate_does_not_create_second_request(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Clicking Follow twice creates only one request."""
    await _register_and_login(client, "dupuser", "dup@example.com")
    cookie_h = _make_cookie_headers(auth_headers)

    await client.post("/users/dupuser/follow", headers=cookie_h, follow_redirects=False)
    await client.post("/users/dupuser/follow", headers=cookie_h, follow_redirects=False)

    count = await _count_follow_requests()
    assert count == 1


async def test_follow_nonexistent_user_returns_404(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Following a nonexistent user returns 404."""
    cookie_h = _make_cookie_headers(auth_headers)
    r = await client.post("/users/doesnotexist/follow", headers=cookie_h, follow_redirects=False)
    assert r.status_code == 404


async def test_follow_requires_login(client: AsyncClient, test_user: dict):
    """Follow without auth redirects to login."""
    r = await client.post("/users/testuser/follow", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


# ============================================================
# Follow — profile page button state
# ============================================================

async def test_profile_shows_follow_button_when_not_following(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Profile shows Follow button when not following."""
    await _register_and_login(client, "targetuser", "target@example.com")
    cookie_h = _make_cookie_headers(auth_headers)

    r = await client.get("/users/targetuser",
                         headers={**cookie_h, "Accept": "text/html"})
    assert r.status_code == 200
    assert '+ Follow' in r.text
    assert 'Requested' not in r.text
    assert '\u2713 Following' not in r.text


async def test_profile_shows_pending_after_follow(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Profile shows Requested after sending follow request."""
    await _register_and_login(client, "pendinguser", "pending@example.com")
    cookie_h = _make_cookie_headers(auth_headers)

    await client.post("/users/pendinguser/follow", headers=cookie_h, follow_redirects=False)

    r = await client.get("/users/pendinguser",
                         headers={**cookie_h, "Accept": "text/html"})
    assert r.status_code == 200
    assert "Requested" in r.text


async def test_profile_shows_following_after_accept(
    client: AsyncClient, test_user: dict, auth_headers: dict, db_session
):
    """Profile shows Following after target accepts."""
    other_headers = await _register_and_login(client, "acceptuser", "accept@example.com")
    cookie_h = _make_cookie_headers(auth_headers)

    await client.post("/users/acceptuser/follow", headers=cookie_h, follow_redirects=False)

    # Find the request via fresh session
    session, engine = await _fresh_session()
    try:
        result = await session.execute(
            select(FollowRequest).where(FollowRequest.status == FollowRequestStatus.PENDING)
        )
        req = result.scalar_one_or_none()
    finally:
        await session.close()
        await engine.dispose()

    assert req is not None, "Follow request was not created"

    other_cookie = _make_cookie_headers(other_headers)
    await client.post(f"/follow-requests/{req.id}/accept",
                      headers=other_cookie, follow_redirects=False)

    r = await client.get("/users/acceptuser",
                         headers={**cookie_h, "Accept": "text/html"})
    assert r.status_code == 200
    assert "\u2713 Following" in r.text


# ============================================================
# Follow — accept / reject
# ============================================================

async def test_accept_follow_request_creates_follower(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Accepting a follow request creates a Follower row."""
    other_headers = await _register_and_login(client, "willfollow", "willfollow@example.com")
    other_cookie = _make_cookie_headers(other_headers)

    await client.post("/users/testuser/follow", headers=other_cookie, follow_redirects=False)

    session, engine = await _fresh_session()
    try:
        result = await session.execute(
            select(FollowRequest).where(FollowRequest.status == FollowRequestStatus.PENDING)
        )
        req = result.scalar_one_or_none()
    finally:
        await session.close()
        await engine.dispose()

    assert req is not None, "Follow request not created"
    req_id = req.id

    testuser_cookie = _make_cookie_headers(auth_headers)
    r = await client.post(f"/follow-requests/{req_id}/accept",
                          headers=testuser_cookie, follow_redirects=False)
    assert r.status_code == 302

    updated = await _read_follow_request(req_id)
    assert updated.status == FollowRequestStatus.ACCEPTED
    assert await _count_followers() == 1


async def test_reject_follow_request_updates_status(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Rejecting a follow request marks it rejected."""
    other_headers = await _register_and_login(client, "willreject", "willreject@example.com")
    other_cookie = _make_cookie_headers(other_headers)

    await client.post("/users/testuser/follow", headers=other_cookie, follow_redirects=False)

    session, engine = await _fresh_session()
    try:
        result = await session.execute(
            select(FollowRequest).where(FollowRequest.status == FollowRequestStatus.PENDING)
        )
        req = result.scalar_one_or_none()
    finally:
        await session.close()
        await engine.dispose()

    assert req is not None
    req_id = req.id

    testuser_cookie = _make_cookie_headers(auth_headers)
    await client.post(f"/follow-requests/{req_id}/reject",
                      headers=testuser_cookie, follow_redirects=False)

    updated = await _read_follow_request(req_id)
    assert updated.status == FollowRequestStatus.REJECTED
    assert await _count_followers() == 0


async def test_accept_wrong_user_cannot_accept(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """A user cannot accept someone else's follow request."""
    other_headers = await _register_and_login(client, "requester2", "requester2@example.com")
    third_headers = await _register_and_login(client, "thirdparty2", "third2@example.com")
    other_cookie = _make_cookie_headers(other_headers)

    await client.post("/users/testuser/follow", headers=other_cookie, follow_redirects=False)

    session, engine = await _fresh_session()
    try:
        result = await session.execute(
            select(FollowRequest).where(FollowRequest.status == FollowRequestStatus.PENDING)
        )
        req = result.scalar_one_or_none()
    finally:
        await session.close()
        await engine.dispose()

    assert req is not None
    req_id = req.id

    third_cookie = _make_cookie_headers(third_headers)
    await client.post(f"/follow-requests/{req_id}/accept",
                      headers=third_cookie, follow_redirects=False)

    updated = await _read_follow_request(req_id)
    assert updated.status == FollowRequestStatus.PENDING


# ============================================================
# Unfollow
# ============================================================

async def test_unfollow_removes_follower_and_request(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Unfollow removes Follower row and FollowRequest."""
    other_headers = await _register_and_login(client, "unfollowme", "unfollowme@example.com")
    cookie_h = _make_cookie_headers(auth_headers)

    await client.post("/users/unfollowme/follow", headers=cookie_h, follow_redirects=False)

    session, engine = await _fresh_session()
    try:
        result = await session.execute(
            select(FollowRequest).where(FollowRequest.status == FollowRequestStatus.PENDING)
        )
        req = result.scalar_one_or_none()
    finally:
        await session.close()
        await engine.dispose()

    assert req is not None

    other_cookie = _make_cookie_headers(other_headers)
    await client.post(f"/follow-requests/{req.id}/accept",
                      headers=other_cookie, follow_redirects=False)

    r = await client.post("/users/unfollowme/unfollow",
                          headers=cookie_h, follow_redirects=False)
    assert r.status_code == 302

    assert await _count_followers() == 0
    assert await _count_follow_requests() == 0


# ============================================================
# AP Accept{Follow} inbox handler
# ============================================================

async def test_inbox_accept_follow_marks_request_accepted(
    client: AsyncClient, test_user: dict, auth_headers: dict, db_session
):
    """Remote Accept{Follow} marks our pending FollowRequest as accepted."""
    follow_id = "https://remote.example.com/users/remoteuser#follow-abc"
    req = FollowRequest(
        followee_id=uuid.UUID(test_user["id"]),
        actor_ap_id="https://pasticcio.localhost/users/testuser",
        actor_inbox="https://pasticcio.localhost/users/testuser/inbox",
        follow_activity_id=follow_id,
        is_local=True,
        requester_id=uuid.UUID(test_user["id"]),
        status=FollowRequestStatus.PENDING,
    )
    db_session.add(req)
    await db_session.flush()
    await db_session.commit()
    req_id = req.id

    r = await _post_to_inbox(client, "testuser", {
        "type": "Accept",
        "actor": "https://remote.example.com/users/remoteuser",
        "object": {
            "type": "Follow",
            "id": follow_id,
            "actor": "https://pasticcio.localhost/users/testuser",
            "object": "https://remote.example.com/users/remoteuser",
        },
    })
    assert r.status_code == 202

    updated = await _read_follow_request(req_id)
    assert updated.status == FollowRequestStatus.ACCEPTED
    assert updated.decided_at is not None


async def test_inbox_accept_follow_fallback_by_actor(
    client: AsyncClient, test_user: dict, auth_headers: dict, db_session
):
    """Accept{Follow} fallback works when no follow_activity_id stored."""
    local_ap = "https://pasticcio.localhost/users/testuser"
    req = FollowRequest(
        followee_id=uuid.UUID(test_user["id"]),
        actor_ap_id=local_ap,
        actor_inbox="https://pasticcio.localhost/users/testuser/inbox",
        follow_activity_id=None,
        is_local=True,
        requester_id=uuid.UUID(test_user["id"]),
        status=FollowRequestStatus.PENDING,
    )
    db_session.add(req)
    await db_session.flush()
    await db_session.commit()
    req_id = req.id

    r = await _post_to_inbox(client, "testuser", {
        "type": "Accept",
        "actor": "https://remote.example.com/users/remoteuser",
        "object": {
            "type": "Follow",
            "id": f"{local_ap}#follow-xyz",
            "actor": local_ap,
            "object": "https://remote.example.com/users/remoteuser",
        },
    })
    assert r.status_code == 202

    updated = await _read_follow_request(req_id)
    assert updated.status == FollowRequestStatus.ACCEPTED


async def test_inbox_accept_nonexistent_request_ignored(
    client: AsyncClient, test_user: dict
):
    """Accept{Follow} with no matching request returns 202 silently."""
    r = await _post_to_inbox(client, "testuser", {
        "type": "Accept",
        "actor": "https://remote.example.com/users/remoteuser",
        "object": {
            "type": "Follow",
            "id": "https://pasticcio.localhost/users/testuser#follow-none",
            "actor": "https://pasticcio.localhost/users/testuser",
        },
    })
    assert r.status_code == 202


# ============================================================
# Notifications — new_follower
# ============================================================

async def test_follow_creates_notification_for_target(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Follow request creates new_follower notification for the target."""
    other_headers = await _register_and_login(client, "notifme", "notifme@example.com")
    other_cookie = _make_cookie_headers(other_headers)

    await client.post("/users/testuser/follow", headers=other_cookie, follow_redirects=False)

    session, engine = await _fresh_session()
    try:
        result = await session.execute(
            select(Notification).where(
                Notification.notification_type == NotificationType.NEW_FOLLOWER
            )
        )
        notif = result.scalar_one_or_none()
    finally:
        await session.close()
        await engine.dispose()

    assert notif is not None
    assert notif.recipient_id == uuid.UUID(test_user["id"])
    assert notif.read_at is None


# ============================================================
# Notifications — new_comment
# ============================================================

async def test_comment_creates_notification_for_recipe_author(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Comment on recipe creates new_comment notification for the author."""
    recipe = await _create_recipe(client, auth_headers)
    other_headers = await _register_and_login(client, "commenter", "commenter@example.com")
    commenter_cookie = _make_cookie_headers(other_headers)

    r = await client.post(
        f"/api/v1/recipes/{recipe['id']}/comments/submit",
        data={"content": "Great recipe!"},
        headers=commenter_cookie,
        follow_redirects=False,
    )
    assert r.status_code == 302

    session, engine = await _fresh_session()
    try:
        result = await session.execute(
            select(Notification).where(
                Notification.notification_type == NotificationType.NEW_COMMENT
            )
        )
        notif = result.scalar_one_or_none()
    finally:
        await session.close()
        await engine.dispose()

    assert notif is not None
    assert notif.recipient_id == uuid.UUID(test_user["id"])
    assert "Follow Test Recipe" in notif.summary


async def test_self_comment_does_not_create_notification(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Commenting on your own recipe does not create a notification."""
    recipe = await _create_recipe(client, auth_headers)
    cookie_h = _make_cookie_headers(auth_headers)

    await client.post(
        f"/api/v1/recipes/{recipe['id']}/comments/submit",
        data={"content": "My own comment"},
        headers=cookie_h,
        follow_redirects=False,
    )

    session, engine = await _fresh_session()
    try:
        result = await session.execute(
            select(Notification).where(
                Notification.notification_type == NotificationType.NEW_COMMENT
            )
        )
        notif = result.scalar_one_or_none()
    finally:
        await session.close()
        await engine.dispose()

    assert notif is None


# ============================================================
# Notifications — unread count
# ============================================================

async def test_unread_count_zero_when_no_notifications(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    cookie_h = _make_cookie_headers(auth_headers)
    r = await client.get("/api/v1/notifications/unread-count", headers=cookie_h)
    assert r.json()["count"] == 0


async def test_unread_count_includes_pending_follow_requests(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Pending follow requests increase the unread count."""
    other_headers = await _register_and_login(client, "counter1", "counter1@example.com")
    other_cookie = _make_cookie_headers(other_headers)
    await client.post("/users/testuser/follow", headers=other_cookie, follow_redirects=False)

    # unread-count uses get_current_user_optional which reads the cookie,
    # not the Bearer header — use cookie headers here too
    cookie_h = _make_cookie_headers(auth_headers)
    r = await client.get("/api/v1/notifications/unread-count", headers=cookie_h)
    assert r.json()["count"] >= 1


async def test_unread_count_zero_for_anonymous(client: AsyncClient):
    r = await client.get("/api/v1/notifications/unread-count")
    assert r.json()["count"] == 0


# ============================================================
# Notifications — page
# ============================================================

async def test_notifications_page_shows_unread(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Notifications page shows unread notifications."""
    # Create a notification via a follow (which we know works)
    other_headers = await _register_and_login(client, "showme", "showme@example.com")
    other_cookie = _make_cookie_headers(other_headers)
    await client.post("/users/testuser/follow", headers=other_cookie, follow_redirects=False)

    cookie_h = _make_cookie_headers(auth_headers)
    r = await client.get("/notifications", headers={**cookie_h, "Accept": "text/html"})
    assert r.status_code == 200
    # Should show the follow request section
    assert "wants to follow you" in r.text


async def test_notifications_page_empty_after_reading(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """After accepting/rejecting all requests and visiting, page is empty."""
    other_headers = await _register_and_login(client, "readme", "readme@example.com")
    other_cookie = _make_cookie_headers(other_headers)
    await client.post("/users/testuser/follow", headers=other_cookie, follow_redirects=False)

    # Find and accept the request
    session, engine = await _fresh_session()
    try:
        result = await session.execute(
            select(FollowRequest).where(FollowRequest.status == FollowRequestStatus.PENDING)
        )
        req = result.scalar_one_or_none()
    finally:
        await session.close()
        await engine.dispose()

    cookie_h = _make_cookie_headers(auth_headers)
    await client.post(f"/follow-requests/{req.id}/accept",
                      headers=cookie_h, follow_redirects=False)

    # Visit notifications — the new_follower notification is still unread
    r1 = await client.get("/notifications", headers={**cookie_h, "Accept": "text/html"})
    assert r1.status_code == 200

    # Visit again — notification marked as read, no pending requests
    r2 = await client.get("/notifications", headers={**cookie_h, "Accept": "text/html"})
    assert r2.status_code == 200
    assert "No notifications yet" in r2.text


# ============================================================
# Feed
# ============================================================

async def test_feed_empty_when_not_following_anyone(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Feed is empty when user follows nobody."""
    cookie_h = _make_cookie_headers(auth_headers)
    r = await client.get("/feed", headers={**cookie_h, "Accept": "text/html"})
    assert r.status_code == 200
    assert "not following" in r.text.lower()


async def test_feed_shows_recipes_from_followed_users(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Feed shows published recipes from followed users."""
    other_headers = await _register_and_login(client, "feedauthor", "feedauthor@example.com")

    # testuser follows feedauthor and feedauthor accepts
    cookie_h = _make_cookie_headers(auth_headers)
    await client.post("/users/feedauthor/follow", headers=cookie_h, follow_redirects=False)

    session, engine = await _fresh_session()
    try:
        result = await session.execute(
            select(FollowRequest).where(FollowRequest.status == FollowRequestStatus.PENDING)
        )
        req = result.scalar_one_or_none()
    finally:
        await session.close()
        await engine.dispose()

    assert req is not None
    other_cookie = _make_cookie_headers(other_headers)
    await client.post(f"/follow-requests/{req.id}/accept",
                      headers=other_cookie, follow_redirects=False)

    await _create_recipe(client, other_headers, title="Feed Recipe")

    r = await client.get("/feed", headers={**cookie_h, "Accept": "text/html"})
    assert r.status_code == 200
    assert "Feed Recipe" in r.text


async def test_feed_does_not_show_drafts(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Feed does not show draft recipes."""
    other_headers = await _register_and_login(client, "draftauthor", "draft@example.com")

    cookie_h = _make_cookie_headers(auth_headers)
    await client.post("/users/draftauthor/follow", headers=cookie_h, follow_redirects=False)

    session, engine = await _fresh_session()
    try:
        result = await session.execute(
            select(FollowRequest).where(FollowRequest.status == FollowRequestStatus.PENDING)
        )
        req = result.scalar_one_or_none()
    finally:
        await session.close()
        await engine.dispose()

    if req:
        other_cookie = _make_cookie_headers(other_headers)
        await client.post(f"/follow-requests/{req.id}/accept",
                          headers=other_cookie, follow_redirects=False)

    await _create_recipe(client, other_headers, publish=False, title="Draft Recipe")

    r = await client.get("/feed", headers={**cookie_h, "Accept": "text/html"})
    assert r.status_code == 200
    assert "Draft Recipe" not in r.text


async def test_feed_requires_login(client: AsyncClient):
    """Feed redirects to login for anonymous users."""
    r = await client.get("/feed", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


# ============================================================
# My-recipes
# ============================================================

async def test_my_recipes_shows_own_published_and_drafts(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    await _create_recipe(client, auth_headers, publish=True, title="Published One")
    await _create_recipe(client, auth_headers, publish=False, title="Draft One")

    cookie_h = _make_cookie_headers(auth_headers)
    r = await client.get("/my-recipes", headers={**cookie_h, "Accept": "text/html"})
    assert r.status_code == 200
    assert "Published One" in r.text
    assert "Draft One" in r.text


async def test_my_recipes_shows_bookmarks(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    await client.post("/api/v1/bookmarks", json={
        "recipe_ap_id": "https://remote.example.com/recipes/pasta",
        "title": "Remote Pasta",
        "author_name": "Chef Remote",
    }, headers=auth_headers)

    cookie_h = _make_cookie_headers(auth_headers)
    r = await client.get("/my-recipes", headers={**cookie_h, "Accept": "text/html"})
    assert r.status_code == 200
    assert "Remote Pasta" in r.text


async def test_my_recipes_empty_state(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    cookie_h = _make_cookie_headers(auth_headers)
    r = await client.get("/my-recipes", headers={**cookie_h, "Accept": "text/html"})
    assert r.status_code == 200
    assert "first recipe" in r.text.lower() or "no recipe" in r.text.lower()


async def test_my_recipes_requires_login(client: AsyncClient):
    r = await client.get("/my-recipes", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


async def test_my_recipes_does_not_show_other_users_recipes(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    other_headers = await _register_and_login(client, "otherrec", "otherrec@example.com")
    await _create_recipe(client, other_headers, title="Other User Recipe")

    cookie_h = _make_cookie_headers(auth_headers)
    r = await client.get("/my-recipes", headers={**cookie_h, "Accept": "text/html"})
    assert r.status_code == 200
    assert "Other User Recipe" not in r.text
