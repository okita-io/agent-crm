"""Append-only research query queue.

Revision ID: o0p1q2r3s4t5
Revises: n9o0p1q2r3s4
Create Date: 2026-08-28 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "o0p1q2r3s4t5"
down_revision: str | None = "n9o0p1q2r3s4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

brand_enum = postgresql.ENUM(
    "MIDNIGHTSATIN",
    "CELESTIAL_NEXUS",
    "HEYBUDDY",
    "TACTIC_STUDIO",
    "UNASSIGNED",
    name="brand",
    create_type=False,
)

research_finding_kind_enum = postgresql.ENUM(
    "competitor",
    "nonprofit",
    "ad_placement",
    "other",
    name="researchfindingkind",
    create_type=False,
)

research_query_status_enum = postgresql.ENUM(
    "pending",
    "running",
    "completed",
    "failed",
    name="researchquerystatus",
    create_type=False,
)


def _ensure_status_enum(bind) -> None:
    if bind.dialect.name == "postgresql":
        research_query_status_enum.create(bind, checkfirst=True)
        return
    sa.Enum(
        "pending",
        "running",
        "completed",
        "failed",
        name="researchquerystatus",
    ).create(bind, checkfirst=True)


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_status_enum(bind)

    kind_type = research_finding_kind_enum
    brand_type = brand_enum
    status_type = research_query_status_enum
    if bind.dialect.name != "postgresql":
        kind_type = sa.Enum(
            "competitor",
            "nonprofit",
            "ad_placement",
            "other",
            name="researchfindingkind",
        )
        brand_type = sa.Enum(
            "MIDNIGHTSATIN",
            "CELESTIAL_NEXUS",
            "HEYBUDDY",
            "TACTIC_STUDIO",
            "UNASSIGNED",
            name="brand",
        )
        status_type = sa.Enum(
            "pending",
            "running",
            "completed",
            "failed",
            name="researchquerystatus",
        )

    op.create_table(
        "research_queries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=128), nullable=False),
        sa.Column("brand", brand_type, nullable=False),
        sa.Column("kind", kind_type, nullable=False),
        sa.Column("status", status_type, nullable=False),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index(
        "ix_research_queries_brand", "research_queries", ["brand"], unique=False
    )
    op.create_index(
        "ix_research_queries_kind", "research_queries", ["kind"], unique=False
    )
    op.create_index(
        "ix_research_queries_status", "research_queries", ["status"], unique=False
    )
    op.create_index(
        "ix_research_queries_status_brand_id",
        "research_queries",
        ["status", "brand", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_research_queries_status_brand_id", table_name="research_queries")
    op.drop_index("ix_research_queries_status", table_name="research_queries")
    op.drop_index("ix_research_queries_kind", table_name="research_queries")
    op.drop_index("ix_research_queries_brand", table_name="research_queries")
    op.drop_table("research_queries")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        research_query_status_enum.drop(bind, checkfirst=True)
    else:
        sa.Enum(name="researchquerystatus").drop(bind, checkfirst=True)
