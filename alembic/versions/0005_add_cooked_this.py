"""add cooked_this table

Revision ID: 0005
Revises: 0004
Create Date: 2024-01-05 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE cookedthisstatustype AS ENUM ('pending', 'published', 'rejected');
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """)

    op.create_table(
        "cooked_this",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recipe_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_ap_id", sa.String(512), nullable=False),
        sa.Column("ap_id", sa.String(512), nullable=True, unique=True),
        sa.Column("in_reply_to", sa.String(512), nullable=True),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cooked_this.id", ondelete="CASCADE"), nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("is_remote", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("status", sa.Text(), nullable=False, server_default="published"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("ALTER TABLE cooked_this ALTER COLUMN status TYPE cookedthisstatustype USING status::cookedthisstatustype")

    op.create_index("ix_cooked_this_recipe_id", "cooked_this", ["recipe_id"])
    op.create_index("ix_cooked_this_author_id", "cooked_this", ["author_id"])
    op.create_index("ix_cooked_this_parent_id", "cooked_this", ["parent_id"])
    op.create_index("ix_cooked_this_status", "cooked_this", ["status"])


def downgrade() -> None:
    op.drop_table("cooked_this")
    op.execute("DROP TYPE IF EXISTS cookedthisstatustype")
