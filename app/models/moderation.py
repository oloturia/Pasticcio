# ============================================================
# app/models/moderation.py — blocks, mutes, instance rules, bookmarks
# ============================================================

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BlockType(str, enum.Enum):
    BLOCK = "block"
    MUTE = "mute"


class RuleType(str, enum.Enum):
    BLOCK = "block"
    ALLOW = "allow"


class UserBlock(Base):
    __tablename__ = "user_blocks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    blocker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # AP ID of the blocked/muted actor (local or remote)
    blocked_ap_id: Mapped[str] = mapped_column(String(512), nullable=False)

    block_type: Mapped[BlockType] = mapped_column(
        Enum(BlockType, name="blocktype", native_enum=False,
             values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    blocker: Mapped["User"] = relationship("User", foreign_keys=[blocker_id])  # noqa: F821

    def __repr__(self) -> str:
        return f"<UserBlock {self.blocker_id} {self.block_type} {self.blocked_ap_id}>"


class InstanceRule(Base):
    __tablename__ = "instance_rules"

    domain: Mapped[str] = mapped_column(String(256), primary_key=True)
    rule_type: Mapped[RuleType] = mapped_column(
        Enum(RuleType, name="ruletype", native_enum=False,
             values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    created_by: Mapped["User | None"] = relationship("User", foreign_keys=[created_by_id])  # noqa: F821

    def __repr__(self) -> str:
        return f"<InstanceRule {self.rule_type} {self.domain}>"


class Bookmark(Base):
    __tablename__ = "bookmarks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # AP ID of the bookmarked recipe (local or remote)
    recipe_ap_id: Mapped[str] = mapped_column(String(512), nullable=False)
    # Cached metadata for offline display
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    author_ap_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])  # noqa: F821

    def __repr__(self) -> str:
        return f"<Bookmark {self.user_id} {self.recipe_ap_id}>"
