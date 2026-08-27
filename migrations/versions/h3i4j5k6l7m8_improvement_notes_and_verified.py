"""Add VERIFIED activity type and agent_improvement_notes table.

Revision ID: h3i4j5k6l7m8
Revises: g2h3i4j5k6l7
Create Date: 2026-08-27 05:40:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h3i4j5k6l7m8"
down_revision: str | None = "g2h3i4j5k6l7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE activitytype ADD VALUE IF NOT EXISTS 'VERIFIED'"
        )

    op.create_table(
        "agent_improvement_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("gap", "performance", "repair", name="improvementnotekind"),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum("info", "warn", "critical", name="improvementnoteseverity"),
            nullable=False,
        ),
        sa.Column(
            "source_agent",
            sa.Enum(
                "job-dispatcher",
                "hunt-loop",
                "research-loop",
                "lead_verifier",
                "spark-queue",
                "orchestrator",
                name="improvementsourceagent",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("suggested_fix", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "investigating",
                "patched",
                "wontfix",
                name="improvementnotestatus",
            ),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_improvement_notes_fingerprint",
        "agent_improvement_notes",
        ["fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_agent_improvement_notes_kind",
        "agent_improvement_notes",
        ["kind"],
        unique=False,
    )
    op.create_index(
        "ix_agent_improvement_notes_severity",
        "agent_improvement_notes",
        ["severity"],
        unique=False,
    )
    op.create_index(
        "ix_agent_improvement_notes_source_agent",
        "agent_improvement_notes",
        ["source_agent"],
        unique=False,
    )
    op.create_index(
        "ix_agent_improvement_notes_status",
        "agent_improvement_notes",
        ["status"],
        unique=False,
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_improvement_notes_fingerprint_open
            ON agent_improvement_notes (fingerprint)
            WHERE status = 'open'
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP INDEX IF EXISTS uq_agent_improvement_notes_fingerprint_open"
        )
    op.drop_index("ix_agent_improvement_notes_fingerprint", table_name="agent_improvement_notes")
    op.drop_index("ix_agent_improvement_notes_status", table_name="agent_improvement_notes")
    op.drop_index(
        "ix_agent_improvement_notes_source_agent",
        table_name="agent_improvement_notes",
    )
    op.drop_index(
        "ix_agent_improvement_notes_severity",
        table_name="agent_improvement_notes",
    )
    op.drop_index("ix_agent_improvement_notes_kind", table_name="agent_improvement_notes")
    op.drop_table("agent_improvement_notes")
    op.execute("DROP TYPE IF EXISTS improvementnotestatus")
    op.execute("DROP TYPE IF EXISTS improvementsourceagent")
    op.execute("DROP TYPE IF EXISTS improvementnoteseverity")
    op.execute("DROP TYPE IF EXISTS improvementnotekind")
    # Postgres enum values cannot be removed safely; leave VERIFIED in activitytype.
