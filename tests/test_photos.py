# ============================================================
# tests/test_photos.py — tests for recipe photo upload
# ============================================================

import io
import uuid

import pytest
from httpx import AsyncClient

# Minimal valid JPEG bytes (1x1 pixel)
TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.\'\",#\x1c\x1c(7),01444\x1f\'9=82<.342\x1e"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xff\xd9"
)

RECIPE_PAYLOAD = {
    "translation": {
        "language": "en",
        "title": "Photo Test Recipe",
        "description": "For photo tests.",
        "steps": [],
    },
    "original_language": "en",
    "ingredients": [],
    "publish": True,
}


async def _create_recipe(client, auth_headers):
    r = await client.post("/api/v1/recipes/", json=RECIPE_PAYLOAD, headers=auth_headers)
    assert r.status_code == 201
    return r.json()


async def _upload_photo(client, recipe_id, auth_headers, is_cover=False, alt_text=None):
    data = {"is_cover": str(is_cover).lower()}
    if alt_text:
        data["alt_text"] = alt_text
    return await client.post(
        f"/api/v1/recipes/{recipe_id}/photos",
        files={"file": ("test.jpg", io.BytesIO(TINY_JPEG), "image/jpeg")},
        data=data,
        headers=auth_headers,
    )


# ============================================================
# Upload tests
# ============================================================

async def test_upload_photo(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Owner can upload a photo and gets a valid response."""
    recipe = await _create_recipe(client, auth_headers)
    response = await _upload_photo(client, recipe["id"], auth_headers)

    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert "url" in data
    assert data["is_cover"] is False
    assert data["recipe_id"] == recipe["id"]


async def test_upload_photo_with_alt_text(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Alt text is stored and returned correctly."""
    recipe = await _create_recipe(client, auth_headers)
    response = await _upload_photo(
        client, recipe["id"], auth_headers, alt_text="A plate of pasta"
    )
    assert response.json()["alt_text"] == "A plate of pasta"


async def test_upload_photo_as_cover(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Uploading with is_cover=true marks it as cover."""
    recipe = await _create_recipe(client, auth_headers)
    response = await _upload_photo(client, recipe["id"], auth_headers, is_cover=True)
    assert response.json()["is_cover"] is True


async def test_upload_photo_cover_demotes_previous(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Setting a new cover demotes the previous cover photo."""
    recipe = await _create_recipe(client, auth_headers)

    first = await _upload_photo(client, recipe["id"], auth_headers, is_cover=True)
    first_id = first.json()["id"]

    second = await _upload_photo(client, recipe["id"], auth_headers, is_cover=True)
    assert second.json()["is_cover"] is True

    # First photo should no longer be cover
    photos = await client.get(f"/api/v1/recipes/{recipe['id']}/photos")
    photo_map = {p["id"]: p for p in photos.json()}
    assert photo_map[first_id]["is_cover"] is False


async def test_upload_photo_unauthenticated(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Uploading without auth returns 401."""
    recipe = await _create_recipe(client, auth_headers)
    response = await client.post(
        f"/api/v1/recipes/{recipe['id']}/photos",
        files={"file": ("test.jpg", io.BytesIO(TINY_JPEG), "image/jpeg")},
    )
    assert response.status_code == 401


async def test_upload_photo_wrong_owner(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Non-owner cannot upload photos."""
    recipe = await _create_recipe(client, auth_headers)

    await client.post("/api/v1/auth/register", json={
        "username": "otheruser",
        "email": "other2@example.com",
        "password": "OtherPass123!",
    })
    login = await client.post("/api/v1/auth/login", data={
        "username": "otheruser",
        "password": "OtherPass123!",
    })
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await _upload_photo(client, recipe["id"], other_headers)
    assert response.status_code == 403


async def test_upload_photo_invalid_type(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Uploading a non-image file returns 400."""
    recipe = await _create_recipe(client, auth_headers)
    response = await client.post(
        f"/api/v1/recipes/{recipe['id']}/photos",
        files={"file": ("test.txt", io.BytesIO(b"not an image"), "text/plain")},
        headers=auth_headers,
    )
    assert response.status_code == 400


async def test_upload_photo_nonexistent_recipe(
    client: AsyncClient, auth_headers: dict
):
    """Uploading to a nonexistent recipe returns 404."""
    response = await _upload_photo(client, str(uuid.uuid4()), auth_headers)
    assert response.status_code == 404


# ============================================================
# List tests
# ============================================================

async def test_list_photos(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Photos are listed for a recipe."""
    recipe = await _create_recipe(client, auth_headers)
    await _upload_photo(client, recipe["id"], auth_headers)
    await _upload_photo(client, recipe["id"], auth_headers)

    response = await client.get(f"/api/v1/recipes/{recipe['id']}/photos")
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_list_photos_cover_first(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Cover photo appears first in the list."""
    recipe = await _create_recipe(client, auth_headers)
    await _upload_photo(client, recipe["id"], auth_headers, is_cover=False)
    await _upload_photo(client, recipe["id"], auth_headers, is_cover=True)

    photos = await client.get(f"/api/v1/recipes/{recipe['id']}/photos")
    assert photos.json()[0]["is_cover"] is True


# ============================================================
# Update tests
# ============================================================

async def test_update_photo_alt_text(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Owner can update alt text of a photo."""
    recipe = await _create_recipe(client, auth_headers)
    photo = (await _upload_photo(client, recipe["id"], auth_headers)).json()

    response = await client.put(
        f"/api/v1/recipes/{recipe['id']}/photos/{photo['id']}",
        json={"alt_text": "Updated alt text"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["alt_text"] == "Updated alt text"


async def test_update_photo_set_cover(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Owner can promote a photo to cover via PUT."""
    recipe = await _create_recipe(client, auth_headers)
    photo = (await _upload_photo(client, recipe["id"], auth_headers)).json()

    response = await client.put(
        f"/api/v1/recipes/{recipe['id']}/photos/{photo['id']}",
        json={"is_cover": True},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["is_cover"] is True


# ============================================================
# Delete tests
# ============================================================

async def test_delete_photo(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Owner can delete a photo."""
    recipe = await _create_recipe(client, auth_headers)
    photo = (await _upload_photo(client, recipe["id"], auth_headers)).json()

    response = await client.delete(
        f"/api/v1/recipes/{recipe['id']}/photos/{photo['id']}",
        headers=auth_headers,
    )
    assert response.status_code == 204

    # Photo should no longer appear in list
    photos = await client.get(f"/api/v1/recipes/{recipe['id']}/photos")
    assert len(photos.json()) == 0


async def test_delete_photo_wrong_owner(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Non-owner cannot delete photos."""
    recipe = await _create_recipe(client, auth_headers)
    photo = (await _upload_photo(client, recipe["id"], auth_headers)).json()

    await client.post("/api/v1/auth/register", json={
        "username": "thirduser",
        "email": "third@example.com",
        "password": "ThirdPass123!",
    })
    login = await client.post("/api/v1/auth/login", data={
        "username": "thirduser",
        "password": "ThirdPass123!",
    })
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.delete(
        f"/api/v1/recipes/{recipe['id']}/photos/{photo['id']}",
        headers=other_headers,
    )
    assert response.status_code == 403
