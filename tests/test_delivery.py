# ============================================================
# tests/test_delivery.py — tests for Celery delivery tasks
# ============================================================
#
# These tests verify that the API correctly enqueues Celery
# tasks when recipes are published/updated/deleted and when
# comments are created. Celery itself is mocked — we only
# check that .delay() is called with the right arguments.

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

RECIPE_PAYLOAD = {
    "translation": {
        "language": "en",
        "title": "Delivery Test Recipe",
        "description": "For delivery tests.",
        "steps": [{"order": 1, "text": "Cook it."}],
    },
    "original_language": "en",
    "ingredients": [],
}


# ============================================================
# Helpers
# ============================================================

async def _create_recipe(client, auth_headers, publish=False):
    payload = {**RECIPE_PAYLOAD, "publish": publish}
    r = await client.post("/api/v1/recipes/", json=payload, headers=auth_headers)
    assert r.status_code == 201
    return r.json()


# ============================================================
# Recipe delivery tests
# ============================================================

async def test_publish_recipe_triggers_delivery(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Publishing a recipe enqueues a 'create' delivery task."""
    mock_task = MagicMock()

    with patch(
        "app.routers.recipes.deliver_to_followers",
        mock_task,
    ):
        recipe = await _create_recipe(client, auth_headers, publish=True)

    mock_task.delay.assert_called_once()
    args = mock_task.delay.call_args
    assert args.kwargs.get("activity_type") == "create" or args[0][1] == "create"
    assert recipe["id"] in str(mock_task.delay.call_args)


async def test_draft_recipe_does_not_trigger_delivery(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Creating a draft recipe does NOT enqueue a delivery task."""
    mock_task = MagicMock()

    with patch(
        "app.routers.recipes.deliver_to_followers",
        mock_task,
    ):
        await _create_recipe(client, auth_headers, publish=False)

    mock_task.delay.assert_not_called()


async def test_update_published_recipe_triggers_delivery(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Updating a published recipe enqueues an 'update' delivery task."""
    recipe = await _create_recipe(client, auth_headers, publish=True)
    mock_task = MagicMock()

    with patch(
        "app.routers.recipes.deliver_to_followers",
        mock_task,
    ):
        response = await client.put(
            f"/api/v1/recipes/{recipe['id']}",
            json={"servings": 4},
            headers=auth_headers,
        )
        assert response.status_code == 200

    mock_task.delay.assert_called_once()
    assert "update" in str(mock_task.delay.call_args)


async def test_update_draft_recipe_does_not_trigger_delivery(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Updating a draft recipe does NOT enqueue a delivery task."""
    recipe = await _create_recipe(client, auth_headers, publish=False)
    mock_task = MagicMock()

    with patch(
        "app.routers.recipes.deliver_to_followers",
        mock_task,
    ):
        await client.put(
            f"/api/v1/recipes/{recipe['id']}",
            json={"servings": 4},
            headers=auth_headers,
        )

    mock_task.delay.assert_not_called()


async def test_publish_via_update_triggers_create_delivery(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Publishing a draft via PUT enqueues a 'create' delivery task."""
    recipe = await _create_recipe(client, auth_headers, publish=False)
    mock_task = MagicMock()

    with patch(
        "app.routers.recipes.deliver_to_followers",
        mock_task,
    ):
        response = await client.put(
            f"/api/v1/recipes/{recipe['id']}",
            json={"publish": True},
            headers=auth_headers,
        )
        assert response.status_code == 200

    mock_task.delay.assert_called_once()
    assert "create" in str(mock_task.delay.call_args)


async def test_delete_published_recipe_triggers_delivery(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Deleting a published recipe enqueues a 'delete' delivery task."""
    recipe = await _create_recipe(client, auth_headers, publish=True)
    mock_task = MagicMock()

    with patch(
        "app.routers.recipes.deliver_to_followers",
        mock_task,
    ):
        response = await client.delete(
            f"/api/v1/recipes/{recipe['id']}",
            headers=auth_headers,
        )
        assert response.status_code == 204

    mock_task.delay.assert_called_once()
    assert "delete" in str(mock_task.delay.call_args)


async def test_delete_draft_recipe_does_not_trigger_delivery(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Deleting a draft recipe does NOT enqueue a delivery task."""
    recipe = await _create_recipe(client, auth_headers, publish=False)
    mock_task = MagicMock()

    with patch(
        "app.routers.recipes.deliver_to_followers",
        mock_task,
    ):
        await client.delete(
            f"/api/v1/recipes/{recipe['id']}",
            headers=auth_headers,
        )

    mock_task.delay.assert_not_called()


# ============================================================
# Comment delivery tests
# ============================================================

async def test_local_comment_triggers_delivery(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """Posting a local comment enqueues a comment delivery task."""
    recipe = await _create_recipe(client, auth_headers, publish=True)
    mock_task = MagicMock()

    with patch(
        "app.routers.comments.deliver_comment_to_followers",
        mock_task,
    ):
        response = await client.post(
            f"/api/v1/recipes/{recipe['id']}/comments",
            json={"content": "I made this!"},
            headers=auth_headers,
        )
        assert response.status_code == 201

    mock_task.delay.assert_called_once()
    # The comment ID should be passed as argument
    comment_id = response.json()["id"]
    assert comment_id in str(mock_task.delay.call_args)


async def test_delivery_broker_unreachable_does_not_break_api(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """If the Celery broker is unreachable, the API still returns 201."""
    mock_task = MagicMock()
    mock_task.delay.side_effect = Exception("Redis connection refused")

    with patch(
        "app.routers.recipes.deliver_to_followers",
        mock_task,
    ):
        recipe = await _create_recipe(client, auth_headers, publish=True)

    # Despite the delivery error, the recipe was created successfully
    assert recipe["status"] == "published"


async def test_comment_delivery_broker_unreachable_does_not_break_api(
    client: AsyncClient, test_user: dict, auth_headers: dict
):
    """If the Celery broker is unreachable, comment creation still returns 201."""
    recipe = await _create_recipe(client, auth_headers, publish=True)
    mock_task = MagicMock()
    mock_task.delay.side_effect = Exception("Redis connection refused")

    with patch(
        "app.routers.comments.deliver_comment_to_followers",
        mock_task,
    ):
        response = await client.post(
            f"/api/v1/recipes/{recipe['id']}/comments",
            json={"content": "I made this!"},
            headers=auth_headers,
        )

    assert response.status_code == 201
