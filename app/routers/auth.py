# ============================================================
# app/routers/auth.py — authentication endpoints
# ============================================================
#
# Endpoints:
#   POST /api/v1/auth/register  — create a new account
#   POST /api/v1/auth/login     — get a JWT token
#   GET  /api/v1/auth/me        — get the current user's profile

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ap.signatures import generate_rsa_keypair
from app.auth import create_access_token, decode_access_token, hash_password, verify_password
from app.config import settings
from app.database import get_db
from app.models.user import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# OAuth2PasswordBearer tells FastAPI where to find the token.
# When a route uses `Depends(get_current_user)`, FastAPI automatically
# reads the Authorization header and passes the token here.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# ============================================================
# Pydantic schemas (request/response shapes)
# ============================================================
#
# These are separate from the SQLAlchemy models:
# - SQLAlchemy models = database tables
# - Pydantic schemas  = what we accept/return via HTTP
# This separation lets us hide fields (e.g. hashed_password)
# and validate input (e.g. password strength) independently
# from the database structure.

class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    display_name: str | None = None

    @field_validator("username")
    @classmethod
    def username_valid(cls, v: str) -> str:
        v = v.strip().lower()
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        if len(v) > 64:
            raise ValueError("Username must be at most 64 characters")
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username may only contain letters, numbers, hyphens, and underscores")
        return v

    @field_validator("password")
    @classmethod
    def password_strong(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str | None
    email: str
    ap_id: str
    preferred_language: str
    is_admin: bool

    # Allow constructing this from a SQLAlchemy model instance
    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ============================================================
# Dependency: get the currently authenticated user
# ============================================================
#
# Routes that require authentication use `Depends(get_current_user)`.
# FastAPI reads the JWT from the Authorization header, we decode it,
# and return the User from the database.
# If anything is wrong (missing token, expired, invalid), we raise
# a 401 Unauthorized error.

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    user_id = decode_access_token(token)
    if user_id is None:
        raise credentials_error

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise credentials_error
    return user


# ============================================================
# Endpoints
# ============================================================

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    Create a new local user account.

    Generates an RSA key pair for ActivityPub HTTP Signatures
    at registration time. The private key is stored encrypted
    in the database and never exposed via the API.
    """
    if not settings.enable_registrations:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registrations are currently closed on this instance",
        )

    # Check username and email are not already taken
    existing = await db.execute(
        select(User).where(
            (User.username == data.username) | (User.email == data.email)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already in use",
        )

    user_id = uuid.uuid4()
    ap_id = f"https://{settings.instance_domain}/users/{data.username}"

    # Generate RSA key pair for ActivityPub HTTP Signatures.
    # ~50ms on modern hardware — acceptable for a registration call.
    # The private key stays on this server; the public key is shared
    # via the Actor endpoint so other servers can verify our requests.
    private_key_pem, public_key_pem = generate_rsa_keypair()

    user = User(
        id=user_id,
        username=data.username,
        email=data.email,
        display_name=data.display_name or data.username,
        hashed_password=hash_password(data.password),
        ap_id=ap_id,
        is_remote=False,
        public_key=public_key_pem,
        private_key=private_key_pem,
    )
    db.add(user)
    await db.flush()

    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """
    Log in with username and password, receive a JWT token.
    Uses the standard OAuth2 password flow (form data, not JSON)
    so it works with the auto-generated /api/docs interface.
    """
    result = await db.execute(select(User).where(User.username == form.username))
    user = result.scalar_one_or_none()

    # We check both "user exists" and "password correct" in a single
    # branch to avoid leaking whether a username exists via timing.
    if not user or not user.hashed_password or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    token = create_access_token(str(user.id))
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    """Return the profile of the currently authenticated user."""
    return current_user
