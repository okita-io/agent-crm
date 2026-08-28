"""Append-only engagement query queue.

Revision ID: p1q2r3s4t5u6
Revises: o0p1q2r3s4t5
Create Date: 2026-08-28 12:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p1q2r3s4t5u6"
down_revision: str | None = "o0p1q2r3s4t5"
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

engagement_query_status_enum = postgresql.ENUM(
    "pending",
    "running",
    "completed",
    "failed",
    name="engagementquerystatus",
    create_type=False,
)


def _ensure_status_enum(bind) -> None:
    if bind.dialect.name == "postgresql":
        engagement_query_status_enum.create(bind, checkfirst=True)
        return
    sa.Enum(
        "pending",
        "running",
        "completed",
        "failed",
        name="engagementquerystatus",
    ).create(bind, checkfirst=True)


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_status_enum(bind)

    brand_type = brand_enum
    status_type = engagement_query_status_enum
    if bind.dialect.name != "postgresql":
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
            name="engagementquerystatus",
        )

    op.create_table(
        "engagement_queries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=128), nullable=False),
        sa.Column("brand", brand_type, nullable=False),
        sa.Column("hunt_resource_id", sa.Integer(), nullable=True),
        sa.Column("status", status_type, nullable=False),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["hunt_resource_id"],
            ["hunt_resources.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index(
        "ix_engagement_queries_brand", "engagement_queries", ["brand"], unique=False
    )
    op.create_index(
        "ix_engagement_queries_status", "engagement_queries", ["status"], unique=False
    )
    op.create_index(
        "ix_engagement_queries_hunt_resource_id",
        "engagement_queries",
        ["hunt_resource_id"],
        unique=False,
    )
    op.create_index(
        "ix_engagement_queries_status_brand_id",
        "engagement_queries",
        ["status", "brand", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_engagement_queries_status_brand_id", table_name="engagement_queries")
    op.drop_index("ix_engagement_queries_hunt_resource_id", table_name="engagement_queries")
    op.drop_index("ix_engagement_queries_status", table_name="engagement_queries")
    op.drop_index("ix_engagement_queries_brand", table_name="engagement_queries")
    op.drop_table("engagement_queries")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        engagement_query_status_enum.drop(bind, checkfirst=True)
    else:
        sa.Enum(name="engagementquerystatus").drop(bind, checkfirst=True)
