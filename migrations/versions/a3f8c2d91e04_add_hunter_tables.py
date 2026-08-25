"""Add hunt query queue and resource collection tables.

Revision ID: a3f8c2d91e04
Revises: a1b2c3d4e5f6
Create Date: 2026-08-25 20:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f8c2d91e04"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hunt_queries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("origin", sa.String(length=128), nullable=False),
        sa.Column(
            "brand",
            sa.Enum(
                "MIDNIGHTSATIN",
                "CELESTIAL_NEXUS",
                "HEYBUDDY",
                "UNASSIGNED",
                name="brand",
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
                name="huntquerystatus",
            ),
            nullable=False,
        ),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    with op.batch_alter_table("hunt_queries", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_hunt_queries_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_hunt_queries_run_id"), ["run_id"], unique=False)

    op.create_table(
        "hunt_resources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column(
            "brand",
            sa.Enum(
                "MIDNIGHTSATIN",
                "CELESTIAL_NEXUS",
                "HEYBUDDY",
                "UNASSIGNED",
                name="brand",
            ),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.Enum(
                "directory",
                "community",
                "newsletter",
                "forum",
                "list",
                "social",
                "other",
                name="huntresourcekind",
            ),
            nullable=False,
        ),
        sa.Column("found_via_query", sa.Text(), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    with op.batch_alter_table("hunt_resources", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_hunt_resources_domain"), ["domain"], unique=False)
        batch_op.create_index(batch_op.f("ix_hunt_resources_brand"), ["brand"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("hunt_resources", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_hunt_resources_brand"))
        batch_op.drop_index(batch_op.f("ix_hunt_resources_domain"))
    op.drop_table("hunt_resources")
    with op.batch_alter_table("hunt_queries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_hunt_queries_run_id"))
        batch_op.drop_index(batch_op.f("ix_hunt_queries_status"))
    op.drop_table("hunt_queries")
    op.execute("DROP TYPE IF EXISTS huntresourcekind")
    op.execute("DROP TYPE IF EXISTS huntquerystatus")
