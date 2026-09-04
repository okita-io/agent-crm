"""Persist per-agent skill pack and module assignments.

Revision ID: y0z1a2b3c4d5
Revises: x9y0z1a2b3c4
Create Date: 2026-09-04 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from agent_crm.agents.registry import KNOWN_AGENT_ROSTER
from agent_crm.skill_catalog import DEFAULT_AGENT_SKILLS

revision: str = "y0z1a2b3c4d5"
down_revision: str | None = "x9y0z1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_skill_profiles",
        sa.Column("agent_name", sa.String(length=64), nullable=False),
        sa.Column("seeded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("agent_name"),
    )
    op.create_table(
        "agent_skills",
        sa.Column("agent_name", sa.String(length=64), nullable=False),
        sa.Column("skill_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("agent_name", "skill_id"),
    )
    op.create_index("ix_agent_skills_skill_id", "agent_skills", ["skill_id"])

    now = datetime.now(UTC)
    profiles = sa.table(
        "agent_skill_profiles",
        sa.column("agent_name", sa.String),
        sa.column("seeded_at", sa.DateTime(timezone=True)),
    )
    skills = sa.table(
        "agent_skills",
        sa.column("agent_name", sa.String),
        sa.column("skill_id", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        profiles,
        [{"agent_name": name, "seeded_at": now} for name in KNOWN_AGENT_ROSTER],
    )
    skill_rows: list[dict[str, object]] = []
    for agent_name, skill_ids in DEFAULT_AGENT_SKILLS.items():
        for skill_id in skill_ids:
            skill_rows.append(
                {
                    "agent_name": agent_name,
                    "skill_id": skill_id,
                    "created_at": now,
                }
            )
    if skill_rows:
        op.bulk_insert(skills, skill_rows)


def downgrade() -> None:
    op.drop_index("ix_agent_skills_skill_id", table_name="agent_skills")
    op.drop_table("agent_skills")
    op.drop_table("agent_skill_profiles")
