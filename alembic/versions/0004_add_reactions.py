"""add reactions table

Revision ID: 0004
Revises: 0003
Create Date: 2024-01-04 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE reactiontype AS ENUM ('like', 'announce');
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """)

    op.create_table(
        "reactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recipe_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_ap_id", sa.String(512), nullable=False),
        sa.Column("reaction_type", sa.Text(), nullable=False),
        sa.Column("activity_ap_id", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("ALTER TABLE reactions ALTER COLUMN reaction_type TYPE reactiontype USING reaction_type::reactiontype")
    op.create_index("ix_reactions_recipe_id", "reactions", ["recipe_id"])
    op.create_unique_constraint(
        "uq_reaction_recipe_actor_type",
        "reactions",
        ["recipe_id", "actor_ap_id", "reaction_type"],
    )


def downgrade() -> None:
    op.drop_table("reactions")
    op.execute("DROP TYPE IF EXISTS reactiontype")
