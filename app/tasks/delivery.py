# ============================================================
# app/tasks/delivery.py — ActivityPub delivery tasks
# ============================================================
#
# These Celery tasks handle the async delivery of AP activities
# to remote servers. They run in the worker process, not in the
# FastAPI process, so slow or failing remote servers never block
# the API response.
#
# Retry strategy: 3 attempts with fixed delays of 1min, 5min, 30min.
# This covers the most common cases: brief network hiccup, server
# restart, or short maintenance window.
# After 3 failures we give up — the remote server is likely down
# for an extended period and will re-sync via outbox polling.
#
# Why not exponential backoff?
# Fixed delays are more predictable and easier to reason about.
# The chosen values (1/5/30 min) cover short outages without
# flooding the queue with tasks that will sit there for hours.

from __future__ import annotations

import json
import logging

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, selectinload

from app.ap.builder import (
    build_create_activity,
    build_delete_activity,
    build_recipe_article,
    build_update_activity,
)
from app.ap.signatures import sign_request
from app.config import settings
from app.worker import celery_app

logger = logging.getLogger(__name__)

# Content type for all AP HTTP requests
AP_CONTENT_TYPE = "application/activity+json"

# Retry delays in seconds: 1 minute, 5 minutes, 30 minutes
RETRY_DELAYS = [60, 300, 1800]


# ============================================================
# Internal helpers
# ============================================================

def _get_sync_db() -> Session:
    """
    Create a synchronous SQLAlchemy session for use inside Celery tasks.

    Celery tasks run in a regular (non-async) context, so we use the
    sync psycopg2 driver instead of asyncpg. The DATABASE_URL uses
    asyncpg (postgresql+asyncpg://...), so we replace the driver part.

    We create a new engine per task call — this is safe because Celery
    tasks are short-lived and we close the session at the end.
    In a high-throughput setup you would use a connection pool instead.
    """
    sync_url = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql+psycopg2://"
    )
    engine = create_engine(sync_url, pool_pre_ping=True)
    return Session(engine)


def _deliver_signed_post(
    inbox_url: str,
    activity: dict,
    private_key_pem: str,
    key_id: str,
) -> bool:
    """
    POST an AP activity to a remote inbox, signed with HTTP Signatures.

    Returns True on success (2xx response), False otherwise.
    We consider 2xx AND 409 Conflict as success — 409 means the remote
    server already has this activity (idempotent delivery).
    """
    body = json.dumps(activity).encode("utf-8")
    headers = sign_request(
        method="post",
        url=inbox_url,
        body=body,
        private_key_pem=private_key_pem,
        key_id=key_id,
    )
    headers["Content-Type"] = AP_CONTENT_TYPE

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(inbox_url, content=body, headers=headers)
            success = response.status_code < 300 or response.status_code == 409
            if not success:
                logger.warning(
                    "Delivery failed: %s returned %d",
                    inbox_url,
                    response.status_code,
                )
            return success
    except httpx.RequestError as exc:
        logger.warning("Delivery error to %s: %s", inbox_url, exc)
        return False


# ============================================================
# Tasks
# ============================================================

@celery_app.task(
    bind=True,           # "self" is the task instance, needed for retry()
    max_retries=3,
    name="delivery.deliver_activity",
)
def deliver_activity(
    self,
    inbox_url: str,
    activity: dict,
    sender_ap_id: str,
) -> None:
    """
    Deliver a single AP activity to a single inbox URL.

    This is the low-level task — it handles exactly one (activity, inbox)
    pair with retries. deliver_to_followers() fans out by calling this
    task once per follower.

    Args:
        inbox_url:    Full URL of the remote inbox to POST to.
        activity:     The AP activity dict (already built by the caller).
        sender_ap_id: AP ID of the sending actor, used to look up their
                      private key from the database.
    """
    db = _get_sync_db()
    try:
        # Import here to avoid circular imports at module load time
        from app.models.user import User

        # Look up the sender to get their private key
        user = db.execute(
            select(User).where(User.ap_id == sender_ap_id)
        ).scalar_one_or_none()

        if user is None or not user.private_key:
            logger.error("deliver_activity: sender %s not found or has no key", sender_ap_id)
            return

        key_id = f"{user.ap_id}#main-key"
        success = _deliver_signed_post(inbox_url, activity, user.private_key, key_id)

        if not success:
            # Determine which retry attempt this is (0-indexed)
            attempt = self.request.retries  # 0, 1, 2
            delay = RETRY_DELAYS[attempt]
            logger.info(
                "Scheduling retry %d/%d for %s in %ds",
                attempt + 1, self.max_retries, inbox_url, delay,
            )
            raise self.retry(countdown=delay)

    finally:
        db.close()


@celery_app.task(name="delivery.deliver_to_followers")
def deliver_to_followers(
    recipe_id: str,
    activity_type: str,  # "create", "update", or "delete"
) -> None:
    """
    Fan out an activity about a recipe to all followers of its author.

    This task:
      1. Loads the recipe and its author from the database
      2. Builds the appropriate AP activity (Create, Update, or Delete)
      3. Fetches all follower inbox URLs
      4. Enqueues one deliver_activity task per unique inbox URL

    We deduplicate inbox URLs before enqueueing — if 10 followers share
    the same sharedInbox (common on large Mastodon instances), we only
    POST once to that inbox rather than 10 times.

    Args:
        recipe_id:     UUID string of the recipe to deliver.
        activity_type: One of "create", "update", "delete".
    """
    import uuid as uuid_mod
    from app.models.recipe import Recipe, RecipeStatus
    from app.models.follower import Follower

    db = _get_sync_db()
    try:
        # Load recipe with all relationships needed for AP serialisation
        recipe = db.execute(
            select(Recipe)
            .where(Recipe.id == uuid_mod.UUID(recipe_id))
            .options(
                selectinload(Recipe.author),
                selectinload(Recipe.translations),
                selectinload(Recipe.ingredients),
                selectinload(Recipe.photos),
            )
        ).scalar_one_or_none()

        if recipe is None:
            logger.error("deliver_to_followers: recipe %s not found", recipe_id)
            return

        author = recipe.author
        actor_url = f"https://{settings.instance_domain}/users/{author.username}"

        # Build the AP activity based on the requested type
        if activity_type == "delete":
            activity = build_delete_activity(actor_url, recipe.ap_id)
        else:
            # For create and update we need a translation.
            # Prefer the author preferred language, fall back to first available.
            if not recipe.translations:
                logger.warning(
                    "deliver_to_followers: recipe %s has no translations, skipping",
                    recipe_id,
                )
                return

            translation = next(
                (t for t in recipe.translations if t.language == author.preferred_language),
                recipe.translations[0],
            )
            article = build_recipe_article(recipe, translation, settings.instance_domain)

            if activity_type == "create":
                activity = build_create_activity(actor_url, article)
            else:
                activity = build_update_activity(actor_url, article)

        # Fetch all follower inbox URLs for this author
        followers = db.execute(
            select(Follower).where(Follower.followee_id == author.id)
        ).scalars().all()

        if not followers:
            logger.debug("deliver_to_followers: no followers for %s", author.username)
            return

        # Deduplicate inbox URLs to avoid sending the same activity multiple
        # times to shared inboxes (e.g. mastodon.social with 1000 users)
        unique_inboxes = list(dict.fromkeys(f.follower_inbox for f in followers))

        logger.info(
            "Delivering %s activity for recipe %s to %d inbox(es)",
            activity_type, recipe_id, len(unique_inboxes),
        )

        for inbox_url in unique_inboxes:
            deliver_activity.delay(
                inbox_url=inbox_url,
                activity=activity,
                sender_ap_id=author.ap_id,
            )

    finally:
        db.close()


@celery_app.task(name="delivery.deliver_comment_to_followers")

def deliver_comment_to_followers(comment_id: str) -> None:
    """
    Deliver a local CookedThis comment to all followers of the recipe author.

    Builds a Create{Note} activity where:
      - inReplyTo points to the recipe AP ID (or parent comment AP ID)
      - to/cc follow standard AP addressing for public replies

    Args:
        comment_id: UUID string of the CookedThis to deliver.
    """
    import uuid as uuid_mod
    from app.ap.builder import build_note_activity
    from app.models.cooked_this import CookedThis
    from app.models.follower import Follower

    db = _get_sync_db()
    try:
        comment = db.execute(
            select(CookedThis).where(
                CookedThis.id == uuid_mod.UUID(comment_id)
            )
        ).scalar_one_or_none()

        if comment is None:
            logger.error("deliver_comment_to_followers: comment %s not found", comment_id)
            return

        # Load the recipe to find the author and their followers
        from app.models.recipe import Recipe
        recipe = db.execute(
            select(Recipe)
            .where(Recipe.id == comment.recipe_id)
            .options(selectinload(Recipe.author))
        ).scalar_one_or_none()

        if recipe is None:
            logger.error(
                "deliver_comment_to_followers: recipe %s not found", comment.recipe_id
            )
            return

        author = recipe.author
        actor_url = f"https://{settings.instance_domain}/users/{author.username}"

        # Build the Create{Note} activity
        activity = build_note_activity(
            actor_url=actor_url,
            note_ap_id=comment.ap_id,
            content=comment.content,
            in_reply_to=comment.in_reply_to,
            recipe_ap_id=recipe.ap_id,
        )

        # Deliver to all unique follower inboxes
        followers = db.execute(
            select(Follower).where(Follower.followee_id == author.id)
        ).scalars().all()

        if not followers:
            logger.debug(
                "deliver_comment_to_followers: no followers for %s", author.username
            )
            return

        unique_inboxes = list(dict.fromkeys(f.follower_inbox for f in followers))

        logger.info(
            "Delivering comment %s to %d inbox(es)", comment_id, len(unique_inboxes)
        )

        for inbox_url in unique_inboxes:
            deliver_activity.delay(
                inbox_url=inbox_url,
                activity=activity,
                sender_ap_id=author.ap_id,
            )

    finally:
        db.close()
