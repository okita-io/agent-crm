"""Catalog high-engagement forums and threads for agent comment drafts.

Revision ID: m8n9o0p1q2r3
Revises: l7m8n9o0p1q2
Create Date: 2026-08-28 03:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m8n9o0p1q2r3"
down_revision: str | None = "l7m8n9o0p1q2"
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

thread_status_enum = postgresql.ENUM(
    "cataloged",
    "queued",
    "scanned",
    "draft_ready",
    name="engagementthreadstatus",
    create_type=False,
)

draft_status_enum = postgresql.ENUM(
    "draft",
    "review",
    "rejected",
    name="engagementdraftstatus",
    create_type=False,
)


def _ensure_enums(bind) -> None:
    if bind.dialect.name == "postgresql":
        thread_status_enum.create(bind, checkfirst=True)
        draft_status_enum.create(bind, checkfirst=True)
        return
    sa.Enum(
        "cataloged",
        "queued",
        "scanned",
        "draft_ready",
        name="engagementthreadstatus",
    ).create(bind, checkfirst=True)
    sa.Enum("draft", "review", "rejected", name="engagementdraftstatus").create(
        bind, checkfirst=True
    )


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_enums(bind)

    op.add_column(
        "hunt_resources",
        sa.Column("engagement_score", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "hunt_resources",
        sa.Column("last_engagement_scan", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "hunt_resources",
        sa.Column("next_engagement_scan", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_hunt_resources_engagement_score",
        "hunt_resources",
        ["engagement_score"],
        unique=False,
    )
    op.create_index(
        "ix_hunt_resources_next_engagement_scan",
        "hunt_resources",
        ["next_engagement_scan"],
        unique=False,
    )

    thread_status = (
        thread_status_enum if bind.dialect.name == "postgresql" else sa.String(length=32)
    )
    draft_status = (
        draft_status_enum if bind.dialect.name == "postgresql" else sa.String(length=32)
    )
    brand_col = brand_enum if bind.dialect.name == "postgresql" else sa.String(length=32)

    op.create_table(
        "engagement_threads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("hunt_resource_id", sa.Integer(), nullable=True),
        sa.Column("brand", brand_col, nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("platform", sa.String(length=64), nullable=True),
        sa.Column("venue_url", sa.String(length=2048), nullable=True),
        sa.Column("popularity_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comment_count", sa.Integer(), nullable=True),
        sa.Column("trend_keywords", sa.JSON(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("found_via_query", sa.Text(), nullable=True),
        sa.Column("status", thread_status, nullable=False, server_default="cataloged"),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["hunt_resource_id"], ["hunt_resources.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_index(
        op.f("ix_engagement_threads_url"), "engagement_threads", ["url"], unique=True
    )
    op.create_index(
        op.f("ix_engagement_threads_hunt_resource_id"),
        "engagement_threads",
        ["hunt_resource_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_engagement_threads_brand"),
        "engagement_threads",
        ["brand"],
        unique=False,
    )
    op.create_index(
        op.f("ix_engagement_threads_platform"),
        "engagement_threads",
        ["platform"],
        unique=False,
    )
    op.create_index(
        op.f("ix_engagement_threads_popularity_score"),
        "engagement_threads",
        ["popularity_score"],
        unique=False,
    )
    op.create_index(
        op.f("ix_engagement_threads_status"),
        "engagement_threads",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_engagement_threads_next_scan_at"),
        "engagement_threads",
        ["next_scan_at"],
        unique=False,
    )

    op.create_table(
        "engagement_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("brand", brand_col, nullable=False),
        sa.Column("draft_text", sa.Text(), nullable=False),
        sa.Column("product_angle", sa.Text(), nullable=True),
        sa.Column("status", draft_status, nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["engagement_threads.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", "brand", name="uq_engagement_drafts_thread_brand"),
    )
    op.create_index(
        op.f("ix_engagement_drafts_thread_id"),
        "engagement_drafts",
        ["thread_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_engagement_drafts_brand"),
        "engagement_drafts",
        ["brand"],
        unique=False,
    )
    op.create_index(
        op.f("ix_engagement_drafts_status"),
        "engagement_drafts",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_engagement_drafts_status"), table_name="engagement_drafts")
    op.drop_index(op.f("ix_engagement_drafts_brand"), table_name="engagement_drafts")
    op.drop_index(op.f("ix_engagement_drafts_thread_id"), table_name="engagement_drafts")
    op.drop_table("engagement_drafts")

    op.drop_index(
        op.f("ix_engagement_threads_next_scan_at"), table_name="engagement_threads"
    )
    op.drop_index(op.f("ix_engagement_threads_status"), table_name="engagement_threads")
    op.drop_index(
        op.f("ix_engagement_threads_popularity_score"), table_name="engagement_threads"
    )
    op.drop_index(op.f("ix_engagement_threads_platform"), table_name="engagement_threads")
    op.drop_index(op.f("ix_engagement_threads_brand"), table_name="engagement_threads")
    op.drop_index(
        op.f("ix_engagement_threads_hunt_resource_id"), table_name="engagement_threads"
    )
    op.drop_index(op.f("ix_engagement_threads_url"), table_name="engagement_threads")
    op.drop_table("engagement_threads")

    op.drop_index("ix_hunt_resources_next_engagement_scan", table_name="hunt_resources")
    op.drop_index("ix_hunt_resources_engagement_score", table_name="hunt_resources")
    op.drop_column("hunt_resources", "next_engagement_scan")
    op.drop_column("hunt_resources", "last_engagement_scan")
    op.drop_column("hunt_resources", "engagement_score")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        draft_status_enum.drop(bind, checkfirst=True)
        thread_status_enum.drop(bind, checkfirst=True)
