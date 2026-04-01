"""add recipe_step_photos table

Revision ID: 0010
Revises: 0009
Create Date: 2024-01-10 00:00:00.000000

Stores one optional photo per recipe step.
Steps are identified by their order number (integer, 1-based)
rather than a UUID, because steps live inside a JSONB array
in recipe_translations and have no separate table rows.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recipe_step_photos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "recipe_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recipes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 1-based step order number — matches the "order" field in the steps JSONB array
        sa.Column("step_order", sa.Integer(), nullable=False),
        # Path relative to MEDIA_ROOT, e.g. "recipes/{uuid}/steps/1.jpg"
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
        "ix_recipe_step_photos_recipe_id",
        "recipe_step_photos",
        ["recipe_id"],
    )

    # One photo per step per recipe — enforced at DB level
    op.create_unique_constraint(
        "uq_recipe_step_photo",
        "recipe_step_photos",
        ["recipe_id", "step_order"],
    )


def downgrade() -> None:
    op.drop_table("recipe_step_photos")
