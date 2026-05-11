"""add forked_from to recipes

Revision ID: 0006
Revises: 0005
Create Date: 2024-01-06 00:00:00.000000

Adds forked_from field to recipes — the AP ID of the original recipe
when this recipe is a fork of another. NULL means original recipe.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column("forked_from", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recipes", "forked_from")
