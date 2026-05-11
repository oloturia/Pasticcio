# ============================================================
# app/models/user.py — User model
# ============================================================
#
# This file defines the `users` table and everything directly
# related to user accounts — both local users (who registered
# on this instance) and remote users (who live on other
# Fediverse servers but interact with us via ActivityPub).

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    # --- Primary key ---
    # We use UUID instead of an auto-increment integer.
    # Reasons: UUIDs are harder to enumerate (an attacker can't
    # just try /users/1, /users/2, ...), and they work better
    # in distributed/federated systems where two instances might
    # both create a user "number 42".
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Identity ---
    # username is unique per instance (e.g. "maria")
    # The full Fediverse handle is username@instance_domain
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # --- ActivityPub identity ---
    # The canonical URL of this actor in the Fediverse.
    # For local users: https://instance.domain/users/maria
    # For remote users: whatever their home server says
    ap_id: Mapped[str] = mapped_column(String(512), unique=True, nullable=False, index=True)

    # RSA key pair used to sign outgoing HTTP requests (ActivityPub
    # requires this to prove that a message really came from us).
    # The public key is shared openly; the private key never leaves
    # this server.
    public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    private_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Local vs remote ---
    # is_remote=True means this user lives on another server.
    # Remote users have no password here — we just store their
    # profile so we can display it when they comment on a recipe.
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    remote_actor_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # --- Authentication (local users only) ---
    # We never store plain-text passwords — only the bcrypt hash.
    email: Mapped[str | None] = mapped_column(String(256), unique=True, nullable=True, index=True)
    hashed_password: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Preferences ---
    preferred_language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    # Flexible JSON blob for UI settings, notification preferences, etc.
    # We use JSONB (PostgreSQL-specific) which is stored in binary format
    # and supports indexing — faster than plain JSON.
    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # --- Timestamps ---
    # func.now() means "use the database server's current time",
    # which is more reliable than Python's datetime.now() because
    # it doesn't depend on the app server's clock.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # --- Relationships ---
    # One user can have many recipes.
    # back_populates="author" means the Recipe model has a corresponding
    # `author` attribute that points back to this User.
    # cascade="all, delete-orphan" means if we delete a user,
    # all their recipes are deleted too.
    recipes: Mapped[list["Recipe"]] = relationship(  # noqa: F821
        "Recipe",
        back_populates="author",
        cascade="all, delete-orphan",
    )

    # --- Follower ---
    followers: Mapped[list["Follower"]] = relationship(
        "Follower", back_populates="followee", cascade="all, delete-orphan"
    )
    def __repr__(self) -> str:
        return f"<User {self.username}>"
