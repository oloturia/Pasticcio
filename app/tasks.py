# ============================================================
# app/tasks.py — Celery tasks (placeholder)
# ============================================================
#
# Async tasks live here. For now just one example task
# to verify that Celery is working.

from app.worker import celery_app


@celery_app.task(name="tasks.ping")
def ping():
    """Test task — verifies that Celery is working."""
    return "pong"


# Real tasks we'll add later:
#
# @celery_app.task
# def deliver_activity(activity: dict, inbox_url: str):
#     """Send an ActivityPub activity to a remote inbox."""
#     pass
#
# @celery_app.task
# def process_incoming_activity(activity: dict):
#     """Process a received AP activity."""
#     pass
#
# @celery_app.task
# def fetch_remote_actor(actor_url: str):
#     """Fetch and update a remote user's profile."""
#     pass
