"""add cooked_this_photos table

Revision ID: 0011
Revises: 0010
Create Date: 2024-01-11 00:00:00.000000

Stores up to 4 photos per CookedThis comment.
Photos are attached to the comment as AP Image attachments
when the activity is federated to remote servers.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cooked_this_photos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "cooked_this_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cooked_this.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Display order (0-based) — up to 4 photos per comment
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        # Path relative to MEDIA_ROOT, e.g. "comments/{uuid}/0.jpg"
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("alt_text", sa.String(256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_cooked_this_photos_cooked_this_id",
        "cooked_this_photos",
        ["cooked_this_id"],
    )


def downgrade() -> None:
    op.drop_table("cooked_this_photos")
