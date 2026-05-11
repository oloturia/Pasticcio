# tests/test_recipe_fork_form.py
"""
Tests for recipe forking via HTML forms.

AUTH NOTE: /recipes/{id}/fork is a browser route that reads the session
cookie, not the Authorization: Bearer header. We use _make_cookie_headers()
to convert the Bearer token to a cookie for these tests.
"""

import pytest
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from httpx import AsyncClient

from app.models import User, Recipe, RecipeTranslation, RecipeIngredient
from app.config import settings

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/pasticcio_dev_test"


# ============================================================
# Helpers
# ============================================================

async def _register_and_login(client, username, email, password="TestPassword123!"):
    """Register and return Bearer headers."""
    r = await client.post("/api/v1/auth/register", json={
        "username": username, "email": email,
        "password": password, "display_name": username.title(),
    })
    assert r.status_code == 201, f"Register failed: {r.text}"
    login = await client.post("/api/v1/auth/login", data={
        "username": username, "password": password,
    })
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _make_cookie_headers(bearer_headers: dict) -> dict:
    """Convert Bearer headers to session cookie (for browser routes)."""
    token = bearer_headers["Authorization"].split("Bearer ")[1]
    return {"Cookie": f"session={token}"}


async def _fresh_query(query):
    """Run a query on a fresh DB connection to see committed data."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        result = await session.execute(query)
        data = result.scalars().all()
    await engine.dispose()
    return data


# ============================================================
# Tests
# ============================================================

async def test_fork_recipe_success(client: AsyncClient, db_session):
    """Forking a published recipe creates a draft copy and redirects to edit."""
    author_h = await _register_and_login(client, "forkauthor", "forkauthor@example.com")
    forker_h = await _register_and_login(client, "forker", "forker@example.com")

    # Get author UUID from API
    me = await client.get("/api/v1/auth/me", headers=author_h)
    author_id = me.json()["id"]

    # Create a published recipe directly in DB
    recipe = Recipe(
        author_id=author_id,
        slug="carbonara-recipe",
        status="published",
        ap_id=f"https://pasticcio.localhost/users/forkauthor/recipes/carbonara",
    )
    db_session.add(recipe)
    await db_session.flush()

    translation = RecipeTranslation(
        recipe_id=recipe.id,
        language="en",
        title="Spaghetti Carbonara",
        description="Classic Italian pasta",
        steps=[{"order": 1, "text": "Boil pasta"}],
    )
    db_session.add(translation)

    ingredient = RecipeIngredient(
        recipe_id=recipe.id,
        quantity=400,
        unit="g",
        name="spaghetti",
        sort_order=1,
    )
    db_session.add(ingredient)
    await db_session.flush()
    await db_session.commit()

    recipe_id = recipe.id

    # Fork via cookie auth (browser route)
    forker_cookie = _make_cookie_headers(forker_h)
    response = await client.post(
        f"/recipes/{recipe_id}/fork",
        headers=forker_cookie,
        follow_redirects=False,
    )

    assert response.status_code == 303, f"Expected 303, got {response.status_code}: {response.text}"
    assert f"/api/v1/recipes/" in response.headers["location"]

    # Verify forked recipe via fresh DB session
    forked = await _fresh_query(
        select(Recipe).where(
            Recipe.forked_from == recipe.ap_id
        )
    )
    assert len(forked) == 1
    forked_recipe = forked[0]
    assert forked_recipe.status.value == "draft"
    assert "forker" in forked_recipe.slug


async def test_fork_recipe_not_found(client: AsyncClient):
    """Forking a nonexistent recipe returns 404."""
    h = await _register_and_login(client, "notfounduser", "notfound@example.com")
    cookie_h = _make_cookie_headers(h)

    response = await client.post(
        f"/recipes/{uuid4()}/fork",
        headers=cookie_h,
        follow_redirects=False,
    )
    assert response.status_code == 404


async def test_fork_draft_recipe_forbidden(client: AsyncClient, db_session):
    """Cannot fork another user's draft recipe."""
    owner_h = await _register_and_login(client, "draftowner", "draftowner@example.com")
    other_h = await _register_and_login(client, "otherfork", "otherfork@example.com")

    me = await client.get("/api/v1/auth/me", headers=owner_h)
    owner_id = me.json()["id"]

    draft = Recipe(
        author_id=owner_id,
        slug="secret-draft",
        status="draft",
        ap_id="https://pasticcio.localhost/users/draftowner/recipes/secret",
    )
    db_session.add(draft)
    await db_session.flush()
    await db_session.commit()
    draft_id = draft.id

    other_cookie = _make_cookie_headers(other_h)
    response = await client.post(
        f"/recipes/{draft_id}/fork",
        headers=other_cookie,
        follow_redirects=False,
    )
    assert response.status_code == 403


async def test_fork_deleted_recipe_not_found(client: AsyncClient, db_session):
    """Deleted recipes cannot be forked."""
    h = await _register_and_login(client, "deleteduser", "deleted@example.com")
    me = await client.get("/api/v1/auth/me", headers=h)
    user_id = me.json()["id"]

    deleted = Recipe(
        author_id=user_id,
        slug="deleted-recipe",
        status="deleted",
        ap_id="https://pasticcio.localhost/users/deleteduser/recipes/deleted",
    )
    db_session.add(deleted)
    await db_session.flush()
    await db_session.commit()
    deleted_id = deleted.id

    cookie_h = _make_cookie_headers(h)
    response = await client.post(
        f"/recipes/{deleted_id}/fork",
        headers=cookie_h,
        follow_redirects=False,
    )
    assert response.status_code == 404


async def test_fork_own_recipe_allowed(client: AsyncClient, db_session):
    """Users can fork their own recipes."""
    h = await _register_and_login(client, "selfforker", "selfforker@example.com")
    me = await client.get("/api/v1/auth/me", headers=h)
    user_id = me.json()["id"]

    recipe = Recipe(
        author_id=user_id,
        slug="my-recipe",
        status="published",
        ap_id="https://pasticcio.localhost/users/selfforker/recipes/myrecipe",
    )
    db_session.add(recipe)
    await db_session.flush()
    await db_session.commit()
    recipe_id = recipe.id

    cookie_h = _make_cookie_headers(h)
    response = await client.post(
        f"/recipes/{recipe_id}/fork",
        headers=cookie_h,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert f"/api/v1/recipes/" in response.headers["location"]


async def test_fork_unauthenticated_fails(client: AsyncClient, db_session, test_user: dict):
    """Unauthenticated fork returns 401."""
    recipe = Recipe(
        author_id=test_user["id"],
        slug="public-recipe",
        status="published",
        ap_id=f"https://pasticcio.localhost/users/testuser/recipes/public",
    )
    db_session.add(recipe)
    await db_session.flush()
    await db_session.commit()
    recipe_id = recipe.id

    # No auth headers at all
    response = await client.post(
        f"/recipes/{recipe_id}/fork",
        follow_redirects=False,
    )
    assert response.status_code == 401


async def test_fork_copies_translations_and_ingredients(client: AsyncClient, db_session):
    """Fork copies all translations and ingredients."""
    author_h = await _register_and_login(client, "copyauthor", "copyauthor@example.com")
    forker_h = await _register_and_login(client, "copyforker", "copyforker@example.com")

    me = await client.get("/api/v1/auth/me", headers=author_h)
    author_id = me.json()["id"]

    recipe = Recipe(
        author_id=author_id,
        slug="complex-recipe",
        status="published",
        ap_id="https://pasticcio.localhost/users/copyauthor/recipes/complex",
    )
    db_session.add(recipe)
    await db_session.flush()

    for lang, title, desc in [
        ("en", "Pasta", "Italian pasta"),
        ("it", "Pasta", "Pasta italiana"),
    ]:
        db_session.add(RecipeTranslation(
            recipe_id=recipe.id,
            language=lang,
            title=title,
            description=desc,
            steps=[{"order": 1, "text": "Cook"}],
        ))

    for i, name in enumerate(["spaghetti", "guanciale", "eggs", "pecorino"], 1):
        db_session.add(RecipeIngredient(
            recipe_id=recipe.id,
            quantity=100 * i,
            unit="g",
            name=name,
            sort_order=i,
        ))

    await db_session.flush()
    await db_session.commit()
    recipe_id = recipe.id
    recipe_ap_id = recipe.ap_id

    forker_cookie = _make_cookie_headers(forker_h)
    response = await client.post(
        f"/recipes/{recipe_id}/fork",
        headers=forker_cookie,
        follow_redirects=False,
    )
    assert response.status_code == 303

    # Verify via fresh session
    forked_recipes = await _fresh_query(
        select(Recipe).where(Recipe.forked_from == recipe_ap_id)
    )
    assert len(forked_recipes) == 1
    new_id = forked_recipes[0].id

    translations = await _fresh_query(
        select(RecipeTranslation).where(RecipeTranslation.recipe_id == new_id)
    )
    assert len(translations) == 2

    ingredients = await _fresh_query(
        select(RecipeIngredient).where(RecipeIngredient.recipe_id == new_id)
    )
    assert len(ingredients) == 4
    names = {ing.name for ing in ingredients}
    assert names == {"spaghetti", "guanciale", "eggs", "pecorino"}
