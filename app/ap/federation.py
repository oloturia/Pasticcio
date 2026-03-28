# ============================================================
# app/ap/federation.py — federation policy enforcement
# ============================================================
#
# This module checks whether an incoming or outgoing activity
# should be allowed based on the instance's federation policy.
#
# FEDERATION_MODE=blacklist (default):
#   All instances are allowed except those in instance_rules with
#   rule_type="block".
#
# FEDERATION_MODE=whitelist:
#   No instances are allowed except those in instance_rules with
#   rule_type="allow". An empty whitelist = fully closed instance.

import logging
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.moderation import InstanceRule, RuleType

logger = logging.getLogger(__name__)


def _domain_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return url


async def is_federation_allowed(actor_url: str, db: AsyncSession) -> bool:
    """
    Check if federation with the given actor's domain is allowed.

    In blacklist mode: allowed unless the domain is blocked.
    In whitelist mode: blocked unless the domain is explicitly allowed.
    """
    domain = _domain_from_url(actor_url)
    if not domain:
        return True

    result = await db.execute(
        select(InstanceRule).where(InstanceRule.domain == domain)
    )
    rule = result.scalar_one_or_none()

    mode = settings.federation_mode.lower()

    if mode == "whitelist":
        # Only allowed if there is an explicit "allow" rule
        if rule and rule.rule_type == RuleType.ALLOW:
            return True
        logger.debug("Federation denied (whitelist): %s", domain)
        return False
    else:
        # Blacklist mode (default): denied only if explicitly blocked
        if rule and rule.rule_type == RuleType.BLOCK:
            logger.debug("Federation denied (blacklist): %s", domain)
            return False
        return True
