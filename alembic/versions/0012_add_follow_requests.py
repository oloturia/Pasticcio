"""add follow_requests table

Revision ID: 0012
Revises: 0011
Create Date: 2024-01-12 00:00:00.000000

Stores pending follow requests for users who have chosen manual approval.
When a Follow activity arrives (local or remote), a row is inserted here
with status='pending'. The followee can then accept or reject it.

On accept: the row moves to status='accepted' and a Follower row is created.
On reject: the row moves to status='rejected' and an optional Reject AP
activity is sent to the remote actor.

For remote followers: actor_ap_id and actor_inbox are the remote actor's
AP ID and inbox URL (fetched from their Actor profile).
For local followers: actor_ap_id is the local user's AP ID,
actor_inbox is their local inbox URL.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE followrequeststatustype AS ENUM ('pending', 'accepted', 'rejected');
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """)

    op.create_table(
        "follow_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # The local user being asked to accept the follow
        sa.Column(
            "followee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # AP ID of the actor who wants to follow (local or remote)
        sa.Column("actor_ap_id", sa.String(512), nullable=False),
        # Inbox URL of the actor — needed to send Accept/Reject back
        sa.Column("actor_inbox", sa.String(512), nullable=False),
        # AP ID of the original Follow activity — used to build Accept/Reject
        sa.Column("follow_activity_id", sa.String(512), nullable=True),
        # Whether the requesting actor is on this instance
        sa.Column("is_local", sa.Boolean(), nullable=False, server_default="false"),
        # If local: the UUID of the requesting user
        sa.Column(
            "requester_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.execute(
        "ALTER TABLE follow_requests ALTER COLUMN status TYPE followrequeststatustype "
        "USING (status::followrequeststatustype);"
    )

    op.create_index("ix_follow_requests_followee_id", "follow_requests", ["followee_id"])
    op.create_index("ix_follow_requests_status", "follow_requests", ["status"])

    # Prevent duplicate pending requests from the same actor to the same user
    op.create_unique_constraint(
        "uq_follow_request_pending",
        "follow_requests",
        ["followee_id", "actor_ap_id"],
    )


def downgrade() -> None:
    op.drop_table("follow_requests")
    op.execute("DROP TYPE IF EXISTS followrequeststatustype")
