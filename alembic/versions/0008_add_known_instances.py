"""add known_instances table

Revision ID: 0008
Revises: 0007
Create Date: 2024-01-08 00:00:00.000000

Tracks Fediverse instances that have interacted with this server.
Used for federated search — we only query Pasticcio instances.

software: name of the AP software (mastodon, pasticcio, pleroma, ...)
version:  software version string
last_seen: last time we received an activity from this instance
is_pasticcio: True if NodeInfo confirms this is a Pasticcio instance
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "known_instances",
        sa.Column("domain", sa.String(256), primary_key=True),
        sa.Column("software", sa.String(64), nullable=True),
        sa.Column("version", sa.String(64), nullable=True),
        sa.Column("is_pasticcio", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "first_seen",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("known_instances")
