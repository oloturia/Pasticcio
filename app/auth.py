# ============================================================
# app/auth.py — authentication utilities
# ============================================================
#
# This module handles two things:
#   1. Password hashing and verification (bcrypt directly)
#   2. JWT creation and validation (python-jose)
#
# We use the `bcrypt` library directly instead of passlib.
# passlib 1.7.x is unmaintained and incompatible with bcrypt >= 4.0
# (bcrypt dropped the __about__ attribute that passlib relied on).
#
# It does NOT handle HTTP — that's the router's job.
# This separation makes it easy to test these functions
# independently from the HTTP layer.

from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.config import settings

# bcrypt work factor (cost parameter).
# 12 is a good default: slow enough to resist brute-force,
# fast enough to not annoy users on login (~300ms on modern hardware).
# Increase this over time as hardware gets faster.
_BCRYPT_ROUNDS = 12


# ============================================================
# Password hashing
# ============================================================
#
# bcrypt has a hard limit of 72 bytes. Passwords longer than
# that are silently truncated by the algorithm, which means
# "password_very_long_A" and "password_very_long_B" would hash
# to the same value if they share the first 72 bytes.
# We enforce the limit explicitly to make the behaviour visible.

def hash_password(plain: str) -> str:
    """Hash a plain-text password with bcrypt. Store the result, never the original."""
    # Encode to bytes (bcrypt works on bytes, not strings)
    password_bytes = plain.encode("utf-8")[:72]
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt(rounds=_BCRYPT_ROUNDS))
    # Decode back to str for storage in the database (VARCHAR column)
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if plain matches the stored bcrypt hash."""
    password_bytes = plain.encode("utf-8")[:72]
    hashed_bytes = hashed.encode("utf-8")
    return bcrypt.checkpw(password_bytes, hashed_bytes)


# ============================================================
# JWT
# ============================================================
#
# A JWT (JSON Web Token) is a signed string that proves identity.
# Structure: header.payload.signature
# The payload contains claims like {"sub": "user-uuid", "exp": ...}
# The signature is created with our SECRET_KEY — only we can create
# valid tokens, but anyone can read the payload (it's base64, not encrypted).
# For sensitive data, use JWE (encrypted JWT) instead.

def create_access_token(user_id: str) -> str:
    """
    Create a JWT access token for the given user ID.
    The token expires after JWT_EXPIRE_MINUTES minutes.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": user_id,                     # "subject" — who this token is for
        "exp": expire,                       # expiry time
        "iat": datetime.now(timezone.utc),   # issued at
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str | None:
    """
    Decode and validate a JWT. Returns the user ID (sub claim)
    if the token is valid, or None if it's invalid or expired.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        user_id: str = payload.get("sub")
        return user_id
    except JWTError:
        return None
