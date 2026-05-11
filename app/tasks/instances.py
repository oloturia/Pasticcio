# ============================================================
# app/tasks/instances.py — Celery task for NodeInfo check
# ============================================================
#
# Runs in the background after we first see a new instance.
# Checks NodeInfo to determine if it's a Pasticcio instance.

import logging

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.ap.instances import fetch_nodeinfo
from app.config import settings
from app.worker import celery_app

logger = logging.getLogger(__name__)


def _get_sync_db() -> Session:
    sync_url = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql+psycopg2://"
    )
    engine = create_engine(sync_url, pool_pre_ping=True)
    return Session(engine)


@celery_app.task(name="instances.check_nodeinfo")
def check_nodeinfo(domain: str) -> None:
    """
    Fetch NodeInfo for a domain and update the known_instances table.
    Determines if the instance runs Pasticcio.
    """
    import asyncio
    from app.models.known_instance import KnownInstance

    # Fetch NodeInfo synchronously via asyncio.run
    try:
        loop = asyncio.new_event_loop()
        nodeinfo = loop.run_until_complete(fetch_nodeinfo(domain))
        loop.close()
    except Exception as exc:
        logger.warning("NodeInfo check failed for %s: %s", domain, exc)
        return

    db = _get_sync_db()
    try:
        instance = db.execute(
            select(KnownInstance).where(KnownInstance.domain == domain)
        ).scalar_one_or_none()

        if not instance:
            instance = KnownInstance(domain=domain)
            db.add(instance)

        if nodeinfo:
            software_info = nodeinfo.get("software", {})
            software_name = software_info.get("name", "").lower()
            instance.software = software_name
            instance.version = software_info.get("version")
            instance.is_pasticcio = (software_name == "pasticcio")
            logger.info(
                "NodeInfo for %s: software=%s, is_pasticcio=%s",
                domain, software_name, instance.is_pasticcio,
            )

        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("NodeInfo DB update failed for %s: %s", domain, exc)
    finally:
        db.close()
