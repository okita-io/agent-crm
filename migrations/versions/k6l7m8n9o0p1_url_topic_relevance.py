"""Add url_topic_relevance table and check_topical_relevance job kind.

Revision ID: k6l7m8n9o0p1
Revises: j5k6l7m8n9o0
Create Date: 2026-08-27 22:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "k6l7m8n9o0p1"
down_revision: str | None = "j5k6l7m8n9o0"
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

topical_verdict_enum = postgresql.ENUM(
    "on_topic",
    "off_topic",
    "uncertain",
    name="topicalrelevanceverdict",
    create_type=False,
)


def _ensure_topical_verdict_enum(bind) -> None:
    """Create the enum when missing; no-op when a worker/create_all raced ahead."""
    if bind.dialect.name == "postgresql":
        topical_verdict_enum.create(bind, checkfirst=True)
        return

    sa.Enum(
        "on_topic",
        "off_topic",
        "uncertain",
        name="topicalrelevanceverdict",
    ).create(bind, checkfirst=True)


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_topical_verdict_enum(bind)

    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE agentjobkind ADD VALUE IF NOT EXISTS 'check_topical_relevance'"
        )

    inspector = inspect(bind)
    if inspector.has_table("url_topic_relevance"):
        return

    op.create_table(
        "url_topic_relevance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("brand", brand_enum, nullable=False),
        sa.Column("verdict", topical_verdict_enum, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_kind", sa.String(length=64), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("page_title", sa.String(length=512), nullable=True),
        sa.Column("page_excerpt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url", "brand", name="uq_url_topic_brand"),
    )
    op.create_index(
        op.f("ix_url_topic_relevance_url"), "url_topic_relevance", ["url"], unique=False
    )
    op.create_index(
        op.f("ix_url_topic_relevance_brand"), "url_topic_relevance", ["brand"], unique=False
    )
    op.create_index(
        op.f("ix_url_topic_relevance_verdict"),
        "url_topic_relevance",
        ["verdict"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("url_topic_relevance"):
        op.drop_index(
            op.f("ix_url_topic_relevance_verdict"), table_name="url_topic_relevance"
        )
        op.drop_index(
            op.f("ix_url_topic_relevance_brand"), table_name="url_topic_relevance"
        )
        op.drop_index(
            op.f("ix_url_topic_relevance_url"), table_name="url_topic_relevance"
        )
        op.drop_table("url_topic_relevance")

    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS topicalrelevanceverdict")
