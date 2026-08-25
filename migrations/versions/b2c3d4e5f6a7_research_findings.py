"""research findings

Revision ID: b2c3d4e5f6a7
Revises: a3f8c2d91e04
Create Date: 2026-08-25 20:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a3f8c2d91e04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Reuse the ``brand`` enum from the initial schema; do not CREATE TYPE again.
brand_enum = postgresql.ENUM(
    "MIDNIGHTSATIN",
    "CELESTIAL_NEXUS",
    "HEYBUDDY",
    "UNASSIGNED",
    name="brand",
    create_type=False,
)

research_finding_kind_enum = postgresql.ENUM(
    "competitor",
    "nonprofit",
    "other",
    name="researchfindingkind",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    research_finding_kind_enum.create(bind, checkfirst=True)

    op.create_table(
        "research_findings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("brand", brand_enum, nullable=False),
        sa.Column("kind", research_finding_kind_enum, nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_query", sa.String(length=500), nullable=False),
        sa.Column("raw_snippet", sa.Text(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_index("ix_research_findings_brand", "research_findings", ["brand"], unique=False)
    op.create_index("ix_research_findings_domain", "research_findings", ["domain"], unique=False)
    op.create_index("ix_research_findings_kind", "research_findings", ["kind"], unique=False)
    op.create_index("ix_research_findings_url", "research_findings", ["url"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_research_findings_url", table_name="research_findings")
    op.drop_index("ix_research_findings_kind", table_name="research_findings")
    op.drop_index("ix_research_findings_domain", table_name="research_findings")
    op.drop_index("ix_research_findings_brand", table_name="research_findings")
    op.drop_table("research_findings")
    bind = op.get_bind()
    research_finding_kind_enum.drop(bind, checkfirst=True)
