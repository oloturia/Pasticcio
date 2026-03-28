"""add user_blocks, instance_rules, bookmarks

Revision ID: 0009
Revises: 0008
Create Date: 2024-01-09 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- block_type enum ---
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE blocktype AS ENUM ('block', 'mute');
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """)

    # --- ruletype enum ---
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE ruletype AS ENUM ('block', 'allow');
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """)

    # --- user_blocks ---
    op.create_table(
        "user_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "blocker_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # blocked_ap_id can be a local or remote actor AP ID
        sa.Column("blocked_ap_id", sa.String(512), nullable=False),
        sa.Column("block_type", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.execute(
        "ALTER TABLE user_blocks ALTER COLUMN block_type TYPE blocktype "
        "USING block_type::blocktype"
    )
    op.create_index("ix_user_blocks_blocker_id", "user_blocks", ["blocker_id"])
    op.create_unique_constraint(
        "uq_user_block", "user_blocks", ["blocker_id", "blocked_ap_id"]
    )

    # --- instance_rules ---
    op.create_table(
        "instance_rules",
        sa.Column("domain", sa.String(256), primary_key=True),
        sa.Column("rule_type", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.execute(
        "ALTER TABLE instance_rules ALTER COLUMN rule_type TYPE ruletype "
        "USING rule_type::ruletype"
    )

    # --- bookmarks ---
    op.create_table(
        "bookmarks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # AP ID of the bookmarked recipe (local or remote)
        sa.Column("recipe_ap_id", sa.String(512), nullable=False),
        # Cached metadata for offline display
        sa.Column("title", sa.String(256), nullable=True),
        sa.Column("author_ap_id", sa.String(512), nullable=True),
        sa.Column("author_name", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_bookmarks_user_id", "bookmarks", ["user_id"])
    op.create_unique_constraint(
        "uq_bookmark", "bookmarks", ["user_id", "recipe_ap_id"]
    )


def downgrade() -> None:
    op.drop_table("bookmarks")
    op.drop_table("instance_rules")
    op.drop_table("user_blocks")
    op.execute("DROP TYPE IF EXISTS ruletype")
    op.execute("DROP TYPE IF EXISTS blocktype")
