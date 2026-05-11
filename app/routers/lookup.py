# ============================================================
# app/routers/lookup.py — remote user and recipe lookup
# ============================================================

import logging
import re
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/lookup", tags=["lookup"])

AP_HEADERS = {"Accept": "application/activity+json"}
TIMEOUT = 10.0


class RemoteRecipeSummary(BaseModel):
    ap_id: str
    title: str | None
    description: str | None
    language: str | None
    servings: int | None
    dietary_tags: list[str]
    prep_time: str | None
    cook_time: str | None


class RemoteUserProfile(BaseModel):
    ap_id: str
    username: str
    display_name: str | None
    bio: str | None
    avatar_url: str | None
    instance_domain: str
    outbox_url: str | None
    recipes: list[RemoteRecipeSummary]
    total_recipes: int
    remote_profile_url: str


class RemoteRecipePreview(BaseModel):
    ap_id: str
    title: str | None
    description: str | None
    language: str | None
    servings: int | None
    dietary_tags: list[str]
    prep_time: str | None
    cook_time: str | None
    author_ap_id: str | None
    author_name: str | None
    instance_domain: str


def _domain_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return url


def _extract_tags_from_ap(obj: dict) -> list[str]:
    tags = []
    for tag in obj.get("tag", []):
        if isinstance(tag, dict) and tag.get("type") == "Hashtag":
            name = tag.get("name", "").lstrip("#").lower()
            if name and name not in ("cookedthis",):
                tags.append(name)
    return tags


def _article_to_summary(obj: dict) -> RemoteRecipeSummary:
    return RemoteRecipeSummary(
        ap_id=obj.get("id", ""),
        title=obj.get("name") or obj.get("pasticcio:title"),
        description=obj.get("summary") or obj.get("content"),
        language=obj.get("inLanguage"),
        servings=obj.get("pasticcio:servings"),
        dietary_tags=_extract_tags_from_ap(obj),
        prep_time=obj.get("pasticcio:prepTime"),
        cook_time=obj.get("pasticcio:cookTime"),
    )


async def _fetch_json(url: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, headers=AP_HEADERS, follow_redirects=True)
            if resp.status_code == 200:
                return resp.json()
    except httpx.RequestError as exc:
        logger.warning("Fetch failed for %s: %s", url, exc)
    return None


async def _webfinger(username: str, domain: str) -> str | None:
    webfinger_url = (
        f"https://{domain}/.well-known/webfinger"
        f"?resource=acct:{username}@{domain}"
    )
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                webfinger_url,
                headers={"Accept": "application/jrd+json"},
                follow_redirects=True,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            for link in data.get("links", []):
                if link.get("rel") == "self" and "application/activity" in link.get("type", ""):
                    return link.get("href")
    except httpx.RequestError as exc:
        logger.warning("WebFinger failed for %s@%s: %s", username, domain, exc)
    return None

@router.get("/")
async def lookup(
    handle: str | None = Query(default=None, description="Fediverse handle e.g. @chef@altro.server"),
    url: str | None = Query(default=None, description="AP URL of a remote recipe"),
):
    """
    Look up a remote Fediverse user or recipe.

    - handle: @username@domain — returns the user's profile and last 10 recipes
    - url: AP ID of a recipe — returns a recipe preview for forking
    """
    if handle and url:
        raise HTTPException(status_code=400, detail="Provide either handle or url, not both")
    if not handle and not url:
        raise HTTPException(status_code=400, detail="Provide either handle or url")

    if handle:
        return await _lookup_user(handle)
    else:
        return await _lookup_recipe(url)


async def _lookup_user(handle: str) -> RemoteUserProfile:
    handle = handle.lstrip("@")
    if "@" not in handle:
        raise HTTPException(status_code=400, detail="Handle must be in format @username@domain")

    username, domain = handle.rsplit("@", 1)

    actor_url = await _webfinger(username, domain)
    if not actor_url:
        raise HTTPException(
            status_code=404,
            detail=f"Could not find user @{username}@{domain}",
        )

    actor = await _fetch_json(actor_url)
    if not actor:
        raise HTTPException(status_code=404, detail="Could not fetch remote user profile")

    display_name = actor.get("name") or username
    bio_html = actor.get("summary", "")
    bio = re.sub(r"<[^>]+>", "", bio_html).strip() if bio_html else None

    avatar_url = None
    icon = actor.get("icon")
    if isinstance(icon, dict):
        avatar_url = icon.get("url")
    elif isinstance(icon, list) and icon:
        avatar_url = icon[0].get("url")

    outbox_url = actor.get("outbox")
    recipes = []
    total_recipes = 0

    if outbox_url:
        outbox = await _fetch_json(outbox_url)
        if outbox:
            total_recipes = outbox.get("totalItems", 0)
            first_page_url = outbox.get("first")
            if isinstance(first_page_url, str):
                first_page = await _fetch_json(first_page_url)
            elif isinstance(first_page_url, dict):
                first_page = first_page_url
            else:
                first_page = outbox

            items = first_page.get("orderedItems", []) if first_page else []
            for item in items[:10]:
                obj = item.get("object", item) if isinstance(item, dict) else None
                if obj and isinstance(obj, dict) and obj.get("type") == "Article":
                    recipes.append(_article_to_summary(obj))

    return RemoteUserProfile(
        ap_id=actor_url,
        username=actor.get("preferredUsername", username),
        display_name=display_name,
        bio=bio,
        avatar_url=avatar_url,
        instance_domain=domain,
        outbox_url=outbox_url,
        recipes=recipes,
        total_recipes=total_recipes,
        remote_profile_url=actor.get("url", actor_url),
    )


async def _lookup_recipe(url: str) -> RemoteRecipePreview:
    obj = await _fetch_json(url)
    if not obj:
        raise HTTPException(status_code=404, detail="Could not fetch remote recipe")

    if obj.get("type") != "Article":
        raise HTTPException(
            status_code=422,
            detail="The provided URL does not point to a recipe (Article)",
        )

    author_ap_id = obj.get("attributedTo")
    author_name = None
    if author_ap_id and isinstance(author_ap_id, str):
        actor = await _fetch_json(author_ap_id)
        if actor:
            author_name = actor.get("name") or actor.get("preferredUsername")

    domain = _domain_from_url(url)

    return RemoteRecipePreview(
        ap_id=obj.get("id", url),
        title=obj.get("name") or obj.get("pasticcio:title"),
        description=obj.get("summary") or obj.get("content"),
        language=obj.get("inLanguage"),
        servings=obj.get("pasticcio:servings"),
        dietary_tags=_extract_tags_from_ap(obj),
        prep_time=obj.get("pasticcio:prepTime"),
        cook_time=obj.get("pasticcio:cookTime"),
        author_ap_id=author_ap_id if isinstance(author_ap_id, str) else None,
        author_name=author_name,
        instance_domain=domain,
    )
