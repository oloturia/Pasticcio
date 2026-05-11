"""add notifications table

Revision ID: 0013
Revises: 0012
Create Date: 2024-01-13 00:00:00.000000

Stores in-app notifications for local users.

Notification types:
  - new_follower: someone sent a follow request
  - new_comment:  someone commented on one of your recipes

We deliberately do NOT notify on Announce (boost) — that would
encourage optimising for virality, which goes against Pasticcio's
philosophy of sharing recipes for their own sake.

Notifications are soft-deleted by setting read_at rather than
removing rows, so the user can see a history of past notifications.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE notificationtype AS ENUM ('new_follower', 'new_comment');
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """)

    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),

        # The local user who receives the notification
        sa.Column(
            "recipient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),

        # Type of event
        sa.Column("notification_type", sa.Text(), nullable=False),

        # AP ID of the actor who triggered the notification
        # (local or remote — stored as string, not FK)
        sa.Column("actor_ap_id", sa.String(512), nullable=False),

        # Optional display name cached at creation time
        # (avoids re-fetching remote actor on every page load)
        sa.Column("actor_display_name", sa.String(128), nullable=True),

        # Context: UUID of the related object (recipe or follow_request)
        # Stored as string to handle both local UUIDs and remote AP IDs
        sa.Column("object_id", sa.String(512), nullable=True),

        # Short human-readable summary cached at creation time
        sa.Column("summary", sa.String(256), nullable=True),

        # NULL = unread, set when the user reads it
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.execute(
        "ALTER TABLE notifications ALTER COLUMN notification_type TYPE notificationtype "
        "USING notification_type::notificationtype"
    )

    op.create_index("ix_notifications_recipient_id", "notifications", ["recipient_id"])
    op.create_index("ix_notifications_read_at", "notifications", ["read_at"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.execute("DROP TYPE IF EXISTS notificationtype")
