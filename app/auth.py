# ============================================================
# app/auth.py — authentication utilities
# ============================================================
#
# This module handles two things:
#   1. Password hashing and verification (bcrypt via passlib)
#   2. JWT creation and validation (python-jose)
#
# It does NOT handle HTTP — that's the router's job.
# This separation makes it easy to test these functions
# independently from the HTTP layer.

from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# --- Password hashing ---
# CryptContext manages the hashing algorithm.
# bcrypt is the current standard for password storage:
# it's deliberately slow to make brute-force attacks expensive.
# deprecated="auto" means old hashes are automatically upgraded
# to bcrypt on next login.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Hash a plain-text password. Store the result, never the original."""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if plain matches the stored hash."""
    return pwd_context.verify(plain, hashed)


# --- JWT ---
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
        "sub": user_id,       # "subject" — who this token is for
        "exp": expire,        # expiry time
        "iat": datetime.now(timezone.utc),  # issued at
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
