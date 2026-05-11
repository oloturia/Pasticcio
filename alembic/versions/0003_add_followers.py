"""add followers table

Revision ID: 0003
Revises: 0002
Create Date: 2024-01-03 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "followers",
        sa.Column("followee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("follower_ap_id", sa.String(512), nullable=False),
        sa.Column("follower_inbox", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_followers_followee_id", "followers", ["followee_id"])
    op.create_unique_constraint("uq_follower_followee", "followers", ["followee_id", "follower_ap_id"])


def downgrade() -> None:
    op.drop_table("followers")
