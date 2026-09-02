"""Catalogued treg endpoints with a paid-tool allowlist.

Revision ID: x9y0z1a2b3c4
Revises: w8x9y0z1a2b3
Create Date: 2026-09-02 12:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "x9y0z1a2b3c4"
down_revision: str | None = "w8x9y0z1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "treg_tools",
        sa.Column("endpoint_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("capability", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("queue_as", sa.String(length=16), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("cost_type", sa.String(length=32), nullable=False),
        sa.Column("cost_note", sa.Text(), nullable=True),
        sa.Column("is_free", sa.Boolean(), nullable=False),
        sa.Column("is_routed", sa.Boolean(), nullable=False),
        sa.Column("selectable", sa.Boolean(), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("endpoint_id"),
    )
    op.create_index("ix_treg_tools_capability", "treg_tools", ["capability"])
    op.create_index("ix_treg_tools_platform", "treg_tools", ["platform"])
    op.create_index("ix_treg_tools_queue_as", "treg_tools", ["queue_as"])
    op.create_index("ix_treg_tools_is_free", "treg_tools", ["is_free"])
    op.create_index("ix_treg_tools_allowed", "treg_tools", ["allowed"])


def downgrade() -> None:
    op.drop_index("ix_treg_tools_allowed", table_name="treg_tools")
    op.drop_index("ix_treg_tools_is_free", table_name="treg_tools")
    op.drop_index("ix_treg_tools_queue_as", table_name="treg_tools")
    op.drop_index("ix_treg_tools_platform", table_name="treg_tools")
    op.drop_index("ix_treg_tools_capability", table_name="treg_tools")
    op.drop_table("treg_tools")
