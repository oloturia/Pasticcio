# ============================================================
# app/models/notification.py — in-app notifications
# ============================================================
#
# Tracks events that a local user should be aware of.
#
# Types:
#   new_follower — someone sent a follow request
#   new_comment  — someone commented on one of your recipes
#
# We deliberately exclude Announce (boost) notifications —
# they would incentivise optimising for virality.

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class NotificationType(str, enum.Enum):
    NEW_FOLLOWER = "new_follower"
    NEW_COMMENT  = "new_comment"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # The local user who receives this notification
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    notification_type: Mapped[NotificationType] = mapped_column(
        Enum(
            NotificationType,
            name="notificationtype",
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )

    # AP ID of the actor who triggered the notification
    actor_ap_id: Mapped[str] = mapped_column(String(512), nullable=False)

    # Display name cached at creation time (avoids re-fetching remote actors)
    actor_display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # UUID or AP ID of the related object (recipe, follow_request, comment)
    object_id: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Short human-readable summary cached at creation time
    # e.g. "commented on Pasta al Pomodoro"
    summary: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # NULL = unread; populated when the user opens the notifications page
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # Relationship back to recipient
    recipient: Mapped["User"] = relationship("User", foreign_keys=[recipient_id])  # noqa: F821

    def __repr__(self) -> str:
        return f"<Notification {self.notification_type} for {self.recipient_id}>"
