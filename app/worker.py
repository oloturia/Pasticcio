# ============================================================
# app/worker.py — Celery configuration
# ============================================================
#
# Celery handles async tasks: primarily sending ActivityPub
# activities to other servers in the Fediverse.
# When a user publishes a recipe, the web server responds
# immediately to the user, then Celery takes care of notifying
# all remote followers in the background.
#
# For now this is just a skeleton. The real tasks (deliver_activity,
# process_incoming_activity, etc.) will be added when we build
# the ActivityPub module.

from celery import Celery

from app.config import settings

# Create the Celery app
# - first argument: module name (used in logs)
# - broker: where Celery reads tasks to execute (Redis)
# - backend: where Celery writes task results (Redis)
celery_app = Celery(
    "pasticcio",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks"],  # Python modules that contain tasks
)

# Celery configuration
celery_app.conf.update(
    # JSON serialisation (safer than the default pickle)
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # If a task fails, retry up to 3 times with backoff
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)
