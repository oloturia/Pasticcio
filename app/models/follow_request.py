# ============================================================
# app/models/follow_request.py — follow request model
# ============================================================
#
# Stores pending follow requests when a user has chosen manual approval.
# Local and remote follow requests go through the same table.
#
# Lifecycle:
#   pending  → followee has not yet decided
#   accepted → followee accepted; a Follower row has been created
#   rejected → followee rejected; a Reject AP activity sent if remote

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FollowRequestStatus(str, enum.Enum):
    PENDING  = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class FollowRequest(Base):
    __tablename__ = "follow_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # The local user who is being asked to accept the follow
    followee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # AP ID of the actor requesting the follow (local or remote)
    actor_ap_id: Mapped[str] = mapped_column(String(512), nullable=False)

    # Inbox URL of the requesting actor — needed to deliver Accept/Reject
    actor_inbox: Mapped[str] = mapped_column(String(512), nullable=False)

    # AP ID of the original Follow activity — used to build Accept/Reject
    follow_activity_id: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # True if the requesting actor is on this instance
    is_local: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # If local: UUID of the requesting user (for UI display)
    requester_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )

    status: Mapped[FollowRequestStatus] = mapped_column(
        Enum(
            FollowRequestStatus,
            name="followrequeststatustype",
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=FollowRequestStatus.PENDING,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    followee: Mapped["User"] = relationship(  # noqa: F821
        "User", foreign_keys=[followee_id]
    )
    requester: Mapped["User | None"] = relationship(  # noqa: F821
        "User", foreign_keys=[requester_id]
    )

    def __repr__(self) -> str:
        return f"<FollowRequest {self.actor_ap_id} → {self.followee_id} [{self.status}]>"
