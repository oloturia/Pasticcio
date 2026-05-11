# ============================================================
# tests/test_federation.py — tests for federation policy enforcement
# ============================================================
#
# Tests cover is_federation_allowed() in both modes:
#
#   BLACKLIST mode (default, FEDERATION_MODE=blacklist):
#     - Domains NOT in instance_rules → allowed
#     - Domains with rule_type="block" → denied (403)
#     - Domains with rule_type="allow" → still allowed (allow rules are
#       irrelevant in blacklist mode — they have no effect)
#
#   WHITELIST mode (FEDERATION_MODE=whitelist):
#     - Empty whitelist → everything denied (fully closed instance)
#     - Domains with rule_type="allow" → allowed
#     - Domains with rule_type="block" → denied (block is redundant but harmless)
#     - Domains NOT in instance_rules → denied
#
# All inbox calls mock verify_request, _fetch_remote_actor, and
# _deliver_activity to isolate the federation policy logic from
# HTTP Signatures, network calls, and Celery delivery.

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.ap.federation import is_federation_allowed
from app.models.moderation import InstanceRule, RuleType


# ============================================================
# Shared test data
# ============================================================

# A minimal Follow activity coming from a remote domain.
# The actor URL determines which domain is checked by is_federation_allowed.
def _follow_from(domain: str) -> dict:
    """Build a Follow activity from a given domain."""
    return {
        "type": "Follow",
        "id": f"https://{domain}/users/chef#follow-1",
        "actor": f"https://{domain}/users/chef",
        "object": "https://pasticcio.localhost/users/testuser",
    }


def _fake_actor(domain: str) -> dict:
    """Build a minimal fake remote actor for a given domain."""
    return {
        "type": "Person",
        "id": f"https://{domain}/users/chef",
        "inbox": f"https://{domain}/users/chef/inbox",
        "publicKey": {
            "id": f"https://{domain}/users/chef#main-key",
            "owner": f"https://{domain}/users/chef",
            "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----",
        },
    }


async def _post_activity(client: AsyncClient, domain: str) -> int:
    """
    POST a Follow activity from the given domain to the testuser inbox.
    Mocks signature verification and remote actor fetch.
    Returns the HTTP status code.
    """
    activity = _follow_from(domain)
    fake_actor = _fake_actor(domain)

    with (
        patch("app.routers.activitypub.verify_request", return_value=True),
        patch(
            "app.routers.activitypub._fetch_remote_actor",
            new=AsyncMock(return_value=fake_actor),
        ),
        patch(
            "app.routers.activitypub._deliver_activity",
            new=AsyncMock(),
        ),
        patch(
            "app.tasks.instances.check_nodeinfo",
            new=AsyncMock(),
        ),
    ):
        response = await client.post(
            "/users/testuser/inbox",
            content=json.dumps(activity),
            headers={"Content-Type": "application/activity+json"},
        )
    return response.status_code


# ============================================================
# Unit tests for is_federation_allowed() directly
# ============================================================
#
# These test the function in isolation, without going through HTTP.
# We insert InstanceRule rows directly into the test database.

class TestIsFederationAllowedBlacklist:
    """Unit tests for is_federation_allowed() in blacklist mode."""

    async def test_unknown_domain_allowed(self, db_session: AsyncSession):
        """In blacklist mode, a domain with no rule is allowed."""
        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "blacklist"
            result = await is_federation_allowed(
                "https://unknown.example.com/users/chef", db_session
            )
        assert result is True

    async def test_blocked_domain_denied(self, db_session: AsyncSession):
        """In blacklist mode, a domain with rule_type=block is denied."""
        db_session.add(InstanceRule(
            domain="badactor.example.com",
            rule_type=RuleType.BLOCK,
            reason="Spam instance",
        ))
        await db_session.flush()

        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "blacklist"
            result = await is_federation_allowed(
                "https://badactor.example.com/users/spammer", db_session
            )
        assert result is False

    async def test_allowed_rule_has_no_effect_in_blacklist(self, db_session: AsyncSession):
        """In blacklist mode, an explicit allow rule is redundant but harmless."""
        db_session.add(InstanceRule(
            domain="friendly.example.com",
            rule_type=RuleType.ALLOW,
        ))
        await db_session.flush()

        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "blacklist"
            result = await is_federation_allowed(
                "https://friendly.example.com/users/chef", db_session
            )
        # The domain is allowed (no block rule), so still True
        assert result is True

    async def test_empty_actor_url_allowed(self, db_session: AsyncSession):
        """An empty or malformed actor URL does not crash — returns True."""
        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "blacklist"
            result = await is_federation_allowed("", db_session)
        assert result is True


class TestIsFederationAllowedWhitelist:
    """Unit tests for is_federation_allowed() in whitelist mode."""

    async def test_empty_whitelist_denies_everything(self, db_session: AsyncSession):
        """
        In whitelist mode with no rules at all, every domain is denied.
        This is the 'fully closed instance' scenario.
        """
        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "whitelist"
            result = await is_federation_allowed(
                "https://anyone.example.com/users/chef", db_session
            )
        assert result is False

    async def test_whitelisted_domain_allowed(self, db_session: AsyncSession):
        """In whitelist mode, a domain with rule_type=allow is permitted."""
        db_session.add(InstanceRule(
            domain="trusted.example.com",
            rule_type=RuleType.ALLOW,
            reason="Partner instance",
        ))
        await db_session.flush()

        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "whitelist"
            result = await is_federation_allowed(
                "https://trusted.example.com/users/chef", db_session
            )
        assert result is True

    async def test_unknown_domain_denied_in_whitelist(self, db_session: AsyncSession):
        """In whitelist mode, a domain not in the list is denied even if not blocked."""
        db_session.add(InstanceRule(
            domain="trusted.example.com",
            rule_type=RuleType.ALLOW,
        ))
        await db_session.flush()

        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "whitelist"
            result = await is_federation_allowed(
                "https://notinlist.example.com/users/chef", db_session
            )
        assert result is False

    async def test_blocked_domain_denied_in_whitelist(self, db_session: AsyncSession):
        """In whitelist mode, a block rule also denies the domain (belt and suspenders)."""
        db_session.add(InstanceRule(
            domain="blocked.example.com",
            rule_type=RuleType.BLOCK,
        ))
        await db_session.flush()

        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "whitelist"
            result = await is_federation_allowed(
                "https://blocked.example.com/users/chef", db_session
            )
        assert result is False

    async def test_multiple_allowed_domains(self, db_session: AsyncSession):
        """In whitelist mode, multiple allowed domains all work independently."""
        for domain in ["alpha.example.com", "beta.example.com", "gamma.example.com"]:
            db_session.add(InstanceRule(
                domain=domain,
                rule_type=RuleType.ALLOW,
            ))
        await db_session.flush()

        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "whitelist"
            for domain in ["alpha.example.com", "beta.example.com", "gamma.example.com"]:
                result = await is_federation_allowed(
                    f"https://{domain}/users/chef", db_session
                )
                assert result is True, f"Expected {domain} to be allowed"

            # A domain not in the list is still denied
            result = await is_federation_allowed(
                "https://delta.example.com/users/chef", db_session
            )
            assert result is False


# ============================================================
# Integration tests — federation policy via the HTTP inbox
# ============================================================
#
# These tests go through the full HTTP stack: POST to /inbox,
# which calls is_federation_allowed() with the real DB.
# We patch settings.federation_mode to switch between modes.

class TestInboxFederationPolicyBlacklist:
    """Integration tests: federation policy applied at the inbox level (blacklist mode)."""

    async def test_blacklist_allows_unknown_domain(
        self, client: AsyncClient, test_user: dict
    ):
        """In blacklist mode, a domain with no rule reaches the inbox normally."""
        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "blacklist"
            status = await _post_activity(client, "unknown.example.com")
        assert status == 202

    async def test_blacklist_blocks_blocked_domain(
        self, client: AsyncClient, test_user: dict, db_session: AsyncSession
    ):
        """In blacklist mode, a blocked domain receives 403."""
        # Insert the block rule directly into the DB
        db_session.add(InstanceRule(
            domain="spam.example.com",
            rule_type=RuleType.BLOCK,
            reason="Known spam instance",
        ))
        await db_session.commit()

        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "blacklist"
            status = await _post_activity(client, "spam.example.com")
        assert status == 403

    async def test_blacklist_allows_non_blocked_domain_when_another_is_blocked(
        self, client: AsyncClient, test_user: dict, db_session: AsyncSession
    ):
        """Blocking one domain does not affect other domains."""
        db_session.add(InstanceRule(
            domain="spam.example.com",
            rule_type=RuleType.BLOCK,
        ))
        await db_session.commit()

        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "blacklist"
            # A different domain should still be allowed
            status = await _post_activity(client, "legit.example.com")
        assert status == 202


class TestInboxFederationPolicyWhitelist:
    """Integration tests: federation policy applied at the inbox level (whitelist mode)."""

    async def test_empty_whitelist_blocks_all(
        self, client: AsyncClient, test_user: dict
    ):
        """
        In whitelist mode with no allow rules, every incoming activity is rejected.
        This is the core 'fully closed instance' scenario.
        """
        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "whitelist"
            status = await _post_activity(client, "anyone.example.com")
        assert status == 403

    async def test_whitelist_allows_approved_domain(
        self, client: AsyncClient, test_user: dict, db_session: AsyncSession
    ):
        """In whitelist mode, a domain with an allow rule can post to the inbox."""
        db_session.add(InstanceRule(
            domain="trusted.example.com",
            rule_type=RuleType.ALLOW,
        ))
        await db_session.commit()

        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "whitelist"
            status = await _post_activity(client, "trusted.example.com")
        assert status == 202

    async def test_whitelist_blocks_unapproved_domain(
        self, client: AsyncClient, test_user: dict, db_session: AsyncSession
    ):
        """In whitelist mode, a domain NOT in the allow list is rejected even if not blocked."""
        # Add an allow rule for a different domain
        db_session.add(InstanceRule(
            domain="trusted.example.com",
            rule_type=RuleType.ALLOW,
        ))
        await db_session.commit()

        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "whitelist"
            # This domain is not in the whitelist
            status = await _post_activity(client, "notlisted.example.com")
        assert status == 403

    async def test_whitelist_mode_different_from_blacklist(
        self, client: AsyncClient, test_user: dict
    ):
        """
        The same domain behaves differently in blacklist vs whitelist mode.
        In blacklist mode it is allowed; in whitelist mode it is denied.
        """
        domain = "someinstance.example.com"

        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "blacklist"
            blacklist_status = await _post_activity(client, domain)

        with patch("app.ap.federation.settings") as mock_settings:
            mock_settings.federation_mode = "whitelist"
            whitelist_status = await _post_activity(client, domain)

        assert blacklist_status == 202   # allowed in blacklist mode
        assert whitelist_status == 403   # denied in whitelist mode (empty whitelist)
