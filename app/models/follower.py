# ============================================================
# app/models/follower.py — Follower relationship model
# ============================================================
#
# Tracks who follows a local user.
# Used by ActivityPub to know where to deliver new activities.
#
# The follower is identified by their AP ID (a URL) rather than
# a FK to the users table, because followers can be remote actors
# from other Fediverse servers — they have no local User row.

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Follower(Base):
    __tablename__ = "followers"

    # The local user being followed
    followee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )

    # The AP ID (URL) of whoever is following — may be a remote actor
    follower_ap_id: Mapped[str] = mapped_column(
        String(512),
        primary_key=True,
        nullable=False,
    )

    # Inbox URL of the follower, cached to avoid re-fetching on delivery
    follower_inbox: Mapped[str] = mapped_column(String(512), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationship back to the local user
    followee: Mapped["User"] = relationship("User", back_populates="followers")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Follower {self.follower_ap_id} → {self.followee_id}>"
