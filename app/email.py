# ============================================================
# app/email.py — email sending utilities
# ============================================================
#
# Uses fastapi-mail to send transactional emails.
# Currently only used for email confirmation on registration.
#
# The FastMail instance is created once at module load time and
# reused across requests (connection pooling is handled internally).
#
# If MAIL_SERVER is not configured, sending is silently skipped
# and a warning is logged — useful for development environments
# where you don't want to set up a real SMTP server.

import logging
import secrets
import uuid

import redis.asyncio as aioredis
from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType

from app.config import settings

logger = logging.getLogger(__name__)

# TTL for email confirmation tokens in Redis (2 hours)
VERIFY_TOKEN_TTL = 60 * 60 * 2

# Redis key prefix for confirmation tokens
VERIFY_KEY_PREFIX = "email_verify:"

# ============================================================
# FastMail configuration
# ============================================================

def _make_mail_config() -> ConnectionConfig | None:
    """
    Build the FastMail ConnectionConfig from settings.
    Returns None if SMTP is not configured (MAIL_SERVER is empty).
    """
    if not settings.mail_server:
        return None
    return ConnectionConfig(
        MAIL_USERNAME=settings.mail_username,
        MAIL_PASSWORD=settings.mail_password,
        MAIL_FROM=settings.mail_from,
        MAIL_FROM_NAME=settings.mail_from_name,
        MAIL_PORT=settings.mail_port,
        MAIL_SERVER=settings.mail_server,
        MAIL_STARTTLS=settings.mail_starttls,
        MAIL_SSL_TLS=settings.mail_ssl_tls,
        USE_CREDENTIALS=bool(settings.mail_username),
        VALIDATE_CERTS=True,
    )


_mail_config = _make_mail_config()
_fastmail = FastMail(_mail_config) if _mail_config else None


# ============================================================
# Redis helper
# ============================================================

_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    """Return the shared async Redis client, creating it if needed."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            encoding="utf-8",
        )
    return _redis


# ============================================================
# Public API
# ============================================================

async def create_verification_token(user_id: uuid.UUID) -> str:
    """
    Generate a secure random token, store it in Redis with a 2-hour TTL,
    and return the token string.

    The Redis key is:  email_verify:{token}
    The Redis value is: {user_id}

    Using secrets.token_urlsafe(32) gives 256 bits of entropy — more than
    enough to make brute-force attacks infeasible.
    """
    token = secrets.token_urlsafe(32)
    redis = _get_redis()
    await redis.set(
        f"{VERIFY_KEY_PREFIX}{token}",
        str(user_id),
        ex=VERIFY_TOKEN_TTL,
    )
    return token


async def consume_verification_token(token: str) -> uuid.UUID | None:
    """
    Look up a verification token in Redis.
    If found, delete it (one-time use) and return the associated user_id.
    If not found or expired, return None.
    """
    redis = _get_redis()
    key = f"{VERIFY_KEY_PREFIX}{token}"
    user_id_str = await redis.get(key)
    if not user_id_str:
        return None
    await redis.delete(key)
    try:
        return uuid.UUID(user_id_str)
    except ValueError:
        return None


async def send_confirmation_email(email: str, username: str, token: str) -> None:
    """
    Send an account confirmation email to the new user.

    If SMTP is not configured (development), logs the verification URL
    to the console instead of sending a real email — so you can still
    test the full registration flow without an SMTP server.
    """
    verify_url = f"https://{settings.instance_domain}/verify?token={token}"

    if _fastmail is None:
        # SMTP not configured — log the URL so developers can test manually
        logger.warning(
            "SMTP not configured. Verification URL for %s: %s",
            username,
            verify_url,
        )
        return

    html_body = f"""
    <p>Hi {username},</p>
    <p>Welcome to <strong>{settings.instance_name}</strong>!</p>
    <p>Please confirm your email address by clicking the link below:</p>
    <p><a href="{verify_url}">{verify_url}</a></p>
    <p>This link expires in 2 hours.</p>
    <p>If you did not create an account, you can ignore this email.</p>
    <hr>
    <p style="color:#999;font-size:0.85em;">
        {settings.instance_name} — {settings.instance_domain}
    </p>
    """

    message = MessageSchema(
        subject=f"Confirm your email — {settings.instance_name}",
        recipients=[email],
        body=html_body,
        subtype=MessageType.html,
    )

    try:
        await _fastmail.send_message(message)
        logger.info("Confirmation email sent to %s", email)
    except Exception as exc:
        # Log but do not crash — the user was already created.
        # They can request a new confirmation email later (future feature).
        logger.error("Failed to send confirmation email to %s: %s", email, exc)
