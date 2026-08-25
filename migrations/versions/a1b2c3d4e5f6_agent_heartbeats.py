"""agent heartbeats

Revision ID: a1b2c3d4e5f6
Revises: 6c1ac6215451
Create Date: 2026-08-25 19:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "6c1ac6215451"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_heartbeats",
        sa.Column("agent_name", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum("idle", "thinking", "working", "blocked", name="agentstatus"),
            nullable=False,
        ),
        sa.Column("task", sa.String(length=255), nullable=True),
        sa.Column("resource", sa.String(length=255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("agent_name"),
    )


def downgrade() -> None:
    op.drop_table("agent_heartbeats")
    op.execute("DROP TYPE IF EXISTS agentstatus")
