# ============================================================
# app/ap/builder.py — ActivityPub object builders
# ============================================================
#
# These functions build the JSON-LD dicts that represent
# ActivityPub objects: Actors, Activities, Articles (recipes).
#
# The output of these functions is what gets sent over the wire
# to remote servers (Mastodon, other Pasticcio instances, etc.)
# and also what gets served from our public AP endpoints.
#
# All objects follow the ActivityStreams 2.0 spec with the
# Mastodon extensions where needed for compatibility.
#
# References:
#   https://www.w3.org/TR/activitystreams-core/
#   https://www.w3.org/TR/activitypub/
#   https://docs.joinmastodon.org/spec/activitypub/

from __future__ import annotations

from typing import TYPE_CHECKING

from app.utils.serializers import _seconds_to_iso8601_duration, _normalise_tag, to_ap_tags

if TYPE_CHECKING:
    from app.models.recipe import Recipe, RecipeTranslation
    from app.models.user import User


# The JSON-LD @context used in all our AP objects.
# We include the standard ActivityStreams context plus our
# custom Pasticcio namespace for recipe-specific properties.
AP_CONTEXT = [
    "https://www.w3.org/ns/activitystreams",
    "https://w3id.org/security/v1",          # needed for publicKey
    {
        "pasticcio": "https://pasticcio.social/ns#",
        "dietaryTags": "pasticcio:dietaryTags",
        "metabolicTags": "pasticcio:metabolicTags",
        "categories": "pasticcio:categories",
        "servings": "pasticcio:servings",
        "prepTime": "pasticcio:prepTime",
        "cookTime": "pasticcio:cookTime",
        "difficulty": "pasticcio:difficulty",
    },
]


# ============================================================
# Actor (User profile)
# ============================================================

def build_actor(user: User, instance_domain: str) -> dict:
    """
    Build an ActivityPub Actor object for a local user.

    This is served at GET /users/{username} with content type
    application/activity+json.

    Mastodon fetches this to display the user profile and to
    know where to send activities (inbox URL) and read published
    activities (outbox URL).
    """
    base_url = f"https://{instance_domain}"
    actor_url = f"{base_url}/users/{user.username}"

    return {
        "@context": AP_CONTEXT,
        "type": "Person",
        "id": actor_url,
        "url": actor_url,
        "preferredUsername": user.username,
        "name": user.display_name or user.username,
        "summary": user.bio or "",
        # inbox: where to POST activities directed to this user
        "inbox": f"{actor_url}/inbox",
        # outbox: where to GET this user's published activities
        "outbox": f"{actor_url}/outbox",
        # followers/following: collections (required by spec)
        "followers": f"{actor_url}/followers",
        "following": f"{actor_url}/following",
        # publicKey: used by receivers to verify our HTTP Signatures
        "publicKey": {
            "id": f"{actor_url}#main-key",
            "owner": actor_url,
            "publicKeyPem": user.public_key or "",
        },
        # endpoints: shared inbox for efficient delivery to many followers
        "endpoints": {
            "sharedInbox": f"{base_url}/inbox",
        },
        # icon (avatar) — only include if set
        **(
            {
                "icon": {
                    "type": "Image",
                    "mediaType": "image/jpeg",
                    "url": user.avatar_url,
                }
            }
            if user.avatar_url
            else {}
        ),
    }


# ============================================================
# Recipe → Article
# ============================================================

def build_recipe_article(
    recipe: Recipe,
    translation: RecipeTranslation,
    instance_domain: str,
) -> dict:
    """
    Build an ActivityPub Article object representing a Recipe.

    We use the Article type (not Note) because recipes are
    structured, long-form content — not microblog posts.
    Mastodon will render them as link cards or embedded articles.

    The recipe's tags (dietary, metabolic, categories) become
    AP Hashtag objects so they're searchable across the federation.
    """
    actor_url = f"https://{instance_domain}/users/{recipe.author.username}"
    recipe_url = f"{actor_url}/recipes/{recipe.slug}"

    # Build a human-readable text summary for clients that don't
    # understand the full Article format (e.g. basic Fediverse apps)
    tag_names = [
        f"#{_normalise_tag(t)}"
        for t in (
            list(recipe.dietary_tags or [])
            + list(recipe.metabolic_tags or [])
            + list(translation.categories or [])
        )
    ]
    hashtag_string = " ".join(dict.fromkeys(tag_names))  # deduplicated
    content_summary = (
        f"{translation.title}\n\n"
        f"{translation.description or ''}\n\n"
        f"{hashtag_string}"
    ).strip()

    # Cover photo attachment
    attachments = []
    if recipe.photos:
        cover = next((p for p in recipe.photos if p.is_cover), recipe.photos[0])
        attachments.append({
            "type": "Image",
            "mediaType": "image/jpeg",
            "url": cover.url,
            "name": cover.alt_text or translation.title,
        })

    article: dict = {
        "@context": AP_CONTEXT,
        "type": "Article",
        "id": recipe_url,
        "url": recipe_url,
        "attributedTo": actor_url,
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
        "cc": [f"{actor_url}/followers"],
        "published": recipe.published_at.isoformat() if recipe.published_at else None,
        "updated": recipe.updated_at.isoformat(),
        # Content fields
        "name": translation.title,              # title of the Article
        "content": content_summary,             # HTML or plain text summary
        "summary": translation.description,     # subtitle / description
        # Hashtag objects for federation search
        "tag": to_ap_tags(recipe, translation, instance_domain),
        # Photo attachments
        "attachment": attachments,
        # Pasticcio-specific extensions (in our custom namespace)
        "dietaryTags": recipe.dietary_tags or [],
        "metabolicTags": recipe.metabolic_tags or [],
        "categories": translation.categories or [],
        "servings": recipe.servings,
    }

    # Add optional time fields only when present
    if recipe.prep_time_seconds:
        article["prepTime"] = _seconds_to_iso8601_duration(recipe.prep_time_seconds)
    if recipe.cook_time_seconds:
        article["cookTime"] = _seconds_to_iso8601_duration(recipe.cook_time_seconds)
    if recipe.difficulty:
        article["difficulty"] = recipe.difficulty.value

    return article


# ============================================================
# Activities
# ============================================================

def build_create_activity(
    actor_url: str,
    obj: dict,
) -> dict:
    """
    Wrap an object (e.g. Article) in a Create activity.

    When we publish a new recipe, we send a Create{Article}
    activity to all followers' inboxes.
    """
    return {
        "@context": AP_CONTEXT,
        "type": "Create",
        "id": f"{obj['id']}#create",
        "actor": actor_url,
        "to": obj.get("to", []),
        "cc": obj.get("cc", []),
        "object": obj,
    }


def build_update_activity(
    actor_url: str,
    obj: dict,
) -> dict:
    """
    Wrap an object in an Update activity.
    Sent when a recipe is edited.
    """
    return {
        "@context": AP_CONTEXT,
        "type": "Update",
        "id": f"{obj['id']}#update",
        "actor": actor_url,
        "to": obj.get("to", []),
        "cc": obj.get("cc", []),
        "object": obj,
    }


def build_delete_activity(
    actor_url: str,
    object_id: str,
) -> dict:
    """
    Build a Delete activity.
    Sent when a recipe is deleted, so remote servers can remove it.
    We send a Tombstone instead of the full object as required by spec.
    """
    return {
        "@context": AP_CONTEXT,
        "type": "Delete",
        "id": f"{object_id}#delete",
        "actor": actor_url,
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
        "object": {
            "type": "Tombstone",
            "id": object_id,
        },
    }


def build_accept_activity(
    actor_url: str,
    follow_activity: dict,
) -> dict:
    """
    Build an Accept{Follow} activity.
    Sent in response to an incoming Follow from a remote actor.
    This confirms the follow and causes the remote server to
    start delivering activities to the follower.
    """
    return {
        "@context": AP_CONTEXT,
        "type": "Accept",
        "id": f"{actor_url}#accept-{follow_activity.get('id', 'follow')}",
        "actor": actor_url,
        "object": follow_activity,
    }


def build_outbox_page(
    actor_url: str,
    activities: list[dict],
    total: int,
    page: int,
    per_page: int,
) -> dict:
    """
    Build an OrderedCollectionPage for the outbox.

    The outbox is an OrderedCollection (the full collection object
    with total count) that paginates via OrderedCollectionPage items.
    Clients fetch the first page to get recent activities.
    """
    return {
        "@context": AP_CONTEXT,
        "type": "OrderedCollectionPage",
        "id": f"{actor_url}/outbox?page={page}",
        "partOf": f"{actor_url}/outbox",
        "totalItems": total,
        "orderedItems": activities,
        **(
            {"next": f"{actor_url}/outbox?page={page + 1}"}
            if (page * per_page) < total
            else {}
        ),
        **(
            {"prev": f"{actor_url}/outbox?page={page - 1}"}
            if page > 1
            else {}
        ),
    }


def build_outbox_collection(actor_url: str, total: int) -> dict:
    """
    Build the root OrderedCollection object for the outbox.
    This is what GET /users/{username}/outbox returns without
    a ?page parameter — it just contains the total count and
    a pointer to the first page.
    """
    return {
        "@context": AP_CONTEXT,
        "type": "OrderedCollection",
        "id": f"{actor_url}/outbox",
        "totalItems": total,
        "first": f"{actor_url}/outbox?page=1",
    }


def build_followers_collection(actor_url: str, total: int) -> dict:
    """Build the followers OrderedCollection (count only, no enumeration for privacy)."""
    return {
        "@context": AP_CONTEXT,
        "type": "OrderedCollection",
        "id": f"{actor_url}/followers",
        "totalItems": total,
    }
