# ============================================================
# app/models/cooked_this.py — CookedThis (federated comments)
# ============================================================
#
# A CookedThis represents both:
#   - A local "I made this" entry submitted via the Pasticcio UI
#   - An incoming AP Note from Mastodon or another Fediverse server
#     that replies to a recipe (Create{Note} with inReplyTo = recipe AP ID)
#
# Thread support: parent_id allows nested replies. A top-level
# CookedThis has parent_id = None and in_reply_to = recipe AP ID.
# A reply has parent_id = the parent CookedThis ID.
#
# Moderation: when COMMENTS_MODERATION=on, incoming federated comments
# arrive with status=pending and are only shown after approval.
# Local comments are always published directly.

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CookedThisStatus(str, enum.Enum):
    PENDING = "pending"      # awaiting moderation
    PUBLISHED = "published"  # visible to everyone
    REJECTED = "rejected"    # hidden by moderator


class CookedThis(Base):
    __tablename__ = "cooked_this"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # The recipe this comment belongs to
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The author — local User FK (nullable for remote authors)
    # For remote authors we store actor_ap_id instead
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # AP ID of the author — always set, whether local or remote.
    # For local users: https://instance/users/username
    # For remote users: their home server actor URL
    actor_ap_id: Mapped[str] = mapped_column(String(512), nullable=False)

    # AP ID of this Note object itself
    # For local: we generate it; for remote: their server provides it
    ap_id: Mapped[str | None] = mapped_column(String(512), nullable=True, unique=True)

    # The AP ID this Note is replying to.
    # Top-level: points to the recipe AP ID
    # Reply: points to the parent Note AP ID
    in_reply_to: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Thread support — parent CookedThis for nested replies
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cooked_this.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # The comment text (HTML or plain text from remote, plain text for local)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Whether this is a federated comment (True) or local submission (False)
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    status: Mapped[CookedThisStatus] = mapped_column(
        Enum(CookedThisStatus, name="cookedthisstatustype", native_enum=False),
        nullable=False,
        default=CookedThisStatus.PUBLISHED,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # --- Relationships ---
    recipe: Mapped["Recipe"] = relationship("Recipe", back_populates="cooked_this")  # noqa: F821
    author: Mapped["User | None"] = relationship("User")  # noqa: F821
    replies: Mapped[list["CookedThis"]] = relationship(
        "CookedThis",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    parent: Mapped["CookedThis | None"] = relationship(
        "CookedThis",
        back_populates="replies",
        remote_side="CookedThis.id",
    )

    def __repr__(self) -> str:
        return f"<CookedThis {self.id} by {self.actor_ap_id}>"
