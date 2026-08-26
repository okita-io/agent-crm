"""Add comment_people table for handle-keyed comment authors.

Revision ID: h3i4j5k6l7m8
Revises: g2h3i4j5k6l7
Create Date: 2026-08-26 23:15:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "h3i4j5k6l7m8"
down_revision: str | None = "g2h3i4j5k6l7"
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

contactaudience_enum = postgresql.ENUM(
    "marketing",
    "influencer",
    "user",
    name="contactaudience",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_table(
            "comment_people",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("platform", sa.String(length=64), nullable=False),
            sa.Column("handle", sa.String(length=128), nullable=False),
            sa.Column("display_name", sa.String(length=255), nullable=True),
            sa.Column("profile_url", sa.String(length=2048), nullable=True),
            sa.Column("brand", brand_enum, nullable=False),
            sa.Column("audience", contactaudience_enum, nullable=True),
            sa.Column("source_urls", sa.JSON(), nullable=True),
            sa.Column("comment_snippets", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "platform",
                "handle",
                name="uq_comment_people_platform_handle",
            ),
        )
    else:
        op.create_table(
            "comment_people",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("platform", sa.String(length=64), nullable=False),
            sa.Column("handle", sa.String(length=128), nullable=False),
            sa.Column("display_name", sa.String(length=255), nullable=True),
            sa.Column("profile_url", sa.String(length=2048), nullable=True),
            sa.Column("brand", sa.String(length=32), nullable=False),
            sa.Column("audience", sa.String(length=32), nullable=True),
            sa.Column("source_urls", sa.JSON(), nullable=True),
            sa.Column("comment_snippets", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "platform",
                "handle",
                name="uq_comment_people_platform_handle",
            ),
        )

    with op.batch_alter_table("comment_people", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_comment_people_brand"), ["brand"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_comment_people_platform"), ["platform"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_comment_people_handle"), ["handle"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_comment_people_audience"), ["audience"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("comment_people", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_comment_people_audience"))
        batch_op.drop_index(batch_op.f("ix_comment_people_handle"))
        batch_op.drop_index(batch_op.f("ix_comment_people_platform"))
        batch_op.drop_index(batch_op.f("ix_comment_people_brand"))
    op.drop_table("comment_people")
