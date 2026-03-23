# ============================================================
# app/models/known_instance.py — known Fediverse instances
# ============================================================

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class KnownInstance(Base):
    __tablename__ = "known_instances"

    # Domain is the primary key (e.g. "mastodon.social")
    domain: Mapped[str] = mapped_column(String(256), primary_key=True)

    # Software info from NodeInfo
    software: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # True if NodeInfo confirms this runs Pasticcio
    # Only Pasticcio instances are queried for federated search
    is_pasticcio: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<KnownInstance {self.domain} ({self.software})>"
