# ============================================================
# app/models/reaction.py — Like and Announce reactions
# ============================================================
#
# Stores incoming Like and Announce (boost) activities from
# remote Fediverse actors.
#
# We use a single table with a type column rather than two
# separate tables because the structure is identical for both
# reaction types. Adding a new type in future (e.g. bookmark)
# only requires a new enum value, not a new table.
#
# The actor is stored as an AP ID string (URL), not a FK to users,
# because remote actors may not have a local User row.

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ReactionType(str, enum.Enum):
    LIKE = "like"
    ANNOUNCE = "announce"


class Reaction(Base):
    __tablename__ = "reactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # The local recipe that was liked or boosted
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The remote (or local) actor who reacted — stored as AP ID URL
    actor_ap_id: Mapped[str] = mapped_column(String(512), nullable=False)

    # like or announce
    reaction_type: Mapped[ReactionType] = mapped_column(
        Enum(ReactionType, name="reactiontype", native_enum=False),
        nullable=False,
    )

    # The AP ID of the activity itself — used to handle Undo correctly.
    # When we receive Undo{Like}, the object.id matches this field.
    activity_ap_id: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationship back to the recipe
    recipe: Mapped["Recipe"] = relationship("Recipe", back_populates="reactions")  # noqa: F821

    # One actor can only react once per recipe per type.
    # This constraint prevents duplicate likes/boosts if the remote
    # server sends the same activity twice.
    __table_args__ = (
        UniqueConstraint(
            "recipe_id", "actor_ap_id", "reaction_type",
            name="uq_reaction_recipe_actor_type",
        ),
    )

    def __repr__(self) -> str:
        return f"<Reaction {self.reaction_type} by {self.actor_ap_id} on {self.recipe_id}>"
