# ============================================================
# app/worker.py — Celery application instance
# ============================================================
#
# This file defines the Celery app that the worker process loads.
# It is kept minimal on purpose: only the Celery instance and its
# configuration live here. The actual tasks are in app/tasks/.
#
# The worker is started by podman-compose with:
#   celery -A app.worker worker --loglevel=info
#
# In the FastAPI app, tasks are imported from app/tasks/ directly
# and called with .delay() or .apply_async().

from celery import Celery

from app.config import settings

# Create the Celery instance.
# The first argument is the name of the module — used in log messages.
# broker: where Celery sends task messages (Redis)
# backend: where Celery stores task results (Redis)
# We use the same Redis instance for both to keep the setup simple.
celery_app = Celery(
    "pasticcio",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    # Serialise messages as JSON (readable, language-agnostic)
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timezone — always UTC in the backend
    timezone="UTC",
    enable_utc=True,

    # If a task is not acknowledged within 30 minutes, re-queue it.
    # Prevents tasks from being lost if the worker crashes mid-execution.
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Automatically discover tasks in app/tasks/
    # Celery will import any module named "tasks" inside installed apps.
    imports=["app.tasks.delivery", "app.tasks.instances"],
    broker_connection_retry_on_startup=True,
)
