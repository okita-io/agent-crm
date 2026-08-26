"""Add agent_jobs task queue.

Revision ID: f1a2b3c4d5e6
Revises: e8f9a0b1c2d3
Create Date: 2026-08-26 22:05:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "enrich_contact",
                "verify_lead",
                "decode_email",
                name="agentjobkind",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "completed",
                "failed",
                name="agentjobstatus",
            ),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_agent_jobs_dedupe_key"),
    )
    op.create_index("ix_agent_jobs_kind", "agent_jobs", ["kind"], unique=False)
    op.create_index("ix_agent_jobs_status", "agent_jobs", ["status"], unique=False)
    op.create_index("ix_agent_jobs_priority", "agent_jobs", ["priority"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_agent_jobs_priority", table_name="agent_jobs")
    op.drop_index("ix_agent_jobs_status", table_name="agent_jobs")
    op.drop_index("ix_agent_jobs_kind", table_name="agent_jobs")
    op.drop_table("agent_jobs")
    op.execute("DROP TYPE IF EXISTS agentjobkind")
    op.execute("DROP TYPE IF EXISTS agentjobstatus")
