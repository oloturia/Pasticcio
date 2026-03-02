"""add followers table

Revision ID: 0003
Revises: 0002
Create Date: 2025-01-01 00:00:00.000000

Adds the `followers` table to track who follows whom.
This is needed by ActivityPub to know where to deliver
new activities (recipes, updates, deletes).

A "follow" relationship means:
  follower_ap_id  → the actor who sent the Follow activity
  followee_id     → the local user being followed

We store the follower as an ap_id string (not a FK to users)
because followers can be remote actors from other servers —
they may not have a local User row.
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
        # The local user being followed
        sa.Column(
            "followee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The actor following them — stored as AP ID (URL) because
        # they may be a remote user with no local row
        sa.Column("follower_ap_id", sa.String(512), nullable=False),
        # The inbox URL of the follower — cached here so we don't
        # need to fetch the remote actor profile on every delivery
        sa.Column("follower_inbox", sa.String(512), nullable=False),
        # When the Follow activity was accepted
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Composite primary key: one follower per followee
        sa.PrimaryKeyConstraint("followee_id", "follower_ap_id"),
    )

    # Index for the most common query: "give me all followers of user X"
    # used when delivering a new recipe to all followers
    op.create_index(
        "ix_followers_followee_id",
        "followers",
        ["followee_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_followers_followee_id", table_name="followers")
    op.drop_table("followers")
