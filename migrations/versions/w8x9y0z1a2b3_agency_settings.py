"""Persist dashboard runtime settings (Spark URL, observer intervals, etc.).

Revision ID: w8x9y0z1a2b3
Revises: v7w8x9y0z1a2
Create Date: 2026-09-01 16:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "w8x9y0z1a2b3"
down_revision: str | None = "v7w8x9y0z1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agency_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("agency_settings")
