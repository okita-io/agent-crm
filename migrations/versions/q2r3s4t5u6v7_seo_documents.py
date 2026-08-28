"""SEO targets, query queue, reviews, and implementation plans.

Revision ID: q2r3s4t5u6v7
Revises: p1q2r3s4t5u6
Create Date: 2026-08-28 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "q2r3s4t5u6v7"
down_revision: str | None = "p1q2r3s4t5u6"
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

seo_target_role_enum = postgresql.ENUM(
    "owned",
    "competitor",
    "prospect",
    name="seotargetrole",
    create_type=False,
)
seo_query_kind_enum = postgresql.ENUM(
    "site_audit",
    "competitor",
    name="seoquerykind",
    create_type=False,
)
seo_query_status_enum = postgresql.ENUM(
    "pending",
    "running",
    "completed",
    "failed",
    name="seoquerystatus",
    create_type=False,
)
seo_review_kind_enum = postgresql.ENUM(
    "site_audit",
    "competitor",
    "keyword",
    "geo",
    "plan_review",
    name="seoreviewkind",
    create_type=False,
)
seo_review_status_enum = postgresql.ENUM(
    "draft",
    "review",
    "accepted",
    "rejected",
    name="seoreviewstatus",
    create_type=False,
)
seo_plan_kind_enum = postgresql.ENUM(
    "technical",
    "content",
    "keyword",
    "geo",
    "on_page",
    "mixed",
    name="seoplankind",
    create_type=False,
)
seo_plan_status_enum = postgresql.ENUM(
    "draft",
    "review",
    "approved",
    "rejected",
    name="seoplanstatus",
    create_type=False,
)

_PG_ENUMS = (
    seo_target_role_enum,
    seo_query_kind_enum,
    seo_query_status_enum,
    seo_review_kind_enum,
    seo_review_status_enum,
    seo_plan_kind_enum,
    seo_plan_status_enum,
)

_SQLITE_ENUMS = (
    ("owned", "competitor", "prospect", "seotargetrole"),
    ("site_audit", "competitor", "seoquerykind"),
    ("pending", "running", "completed", "failed", "seoquerystatus"),
    ("site_audit", "competitor", "keyword", "geo", "plan_review", "seoreviewkind"),
    ("draft", "review", "accepted", "rejected", "seoreviewstatus"),
    ("technical", "content", "keyword", "geo", "on_page", "mixed", "seoplankind"),
    ("draft", "review", "approved", "rejected", "seoplanstatus"),
)


def _ensure_enums(bind) -> None:
    if bind.dialect.name == "postgresql":
        for enum in _PG_ENUMS:
            enum.create(bind, checkfirst=True)
        return
    for *values, name in _SQLITE_ENUMS:
        sa.Enum(*values, name=name).create(bind, checkfirst=True)


def _types(bind):
    if bind.dialect.name == "postgresql":
        return {
            "brand": brand_enum,
            "role": seo_target_role_enum,
            "query_kind": seo_query_kind_enum,
            "query_status": seo_query_status_enum,
            "review_kind": seo_review_kind_enum,
            "review_status": seo_review_status_enum,
            "plan_kind": seo_plan_kind_enum,
            "plan_status": seo_plan_status_enum,
        }
    brand = sa.Enum(
        "MIDNIGHTSATIN",
        "CELESTIAL_NEXUS",
        "HEYBUDDY",
        "TACTIC_STUDIO",
        "UNASSIGNED",
        name="brand",
    )
    return {
        "brand": brand,
        "role": sa.Enum("owned", "competitor", "prospect", name="seotargetrole"),
        "query_kind": sa.Enum("site_audit", "competitor", name="seoquerykind"),
        "query_status": sa.Enum(
            "pending", "running", "completed", "failed", name="seoquerystatus"
        ),
        "review_kind": sa.Enum(
            "site_audit",
            "competitor",
            "keyword",
            "geo",
            "plan_review",
            name="seoreviewkind",
        ),
        "review_status": sa.Enum(
            "draft", "review", "accepted", "rejected", name="seoreviewstatus"
        ),
        "plan_kind": sa.Enum(
            "technical",
            "content",
            "keyword",
            "geo",
            "on_page",
            "mixed",
            name="seoplankind",
        ),
        "plan_status": sa.Enum(
            "draft", "review", "approved", "rejected", name="seoplanstatus"
        ),
    }


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_enums(bind)
    types = _types(bind)

    op.create_table(
        "seo_targets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("brand", types["brand"], nullable=False),
        sa.Column("role", types["role"], nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url", "brand", name="uq_seo_targets_url_brand"),
    )
    op.create_index("ix_seo_targets_url", "seo_targets", ["url"], unique=False)
    op.create_index("ix_seo_targets_domain", "seo_targets", ["domain"], unique=False)
    op.create_index("ix_seo_targets_brand", "seo_targets", ["brand"], unique=False)
    op.create_index("ix_seo_targets_role", "seo_targets", ["role"], unique=False)
    op.create_index(
        "ix_seo_targets_next_review_at", "seo_targets", ["next_review_at"], unique=False
    )

    op.create_table(
        "seo_queries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=128), nullable=False),
        sa.Column("brand", types["brand"], nullable=False),
        sa.Column("kind", types["query_kind"], nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("status", types["query_status"], nullable=False),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["target_id"], ["seo_targets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_seo_queries_brand", "seo_queries", ["brand"], unique=False)
    op.create_index("ix_seo_queries_kind", "seo_queries", ["kind"], unique=False)
    op.create_index("ix_seo_queries_target_id", "seo_queries", ["target_id"], unique=False)
    op.create_index("ix_seo_queries_status", "seo_queries", ["status"], unique=False)
    op.create_index(
        "ix_seo_queries_status_brand_id",
        "seo_queries",
        ["status", "brand", "id"],
        unique=False,
    )

    op.create_table(
        "seo_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("brand", types["brand"], nullable=False),
        sa.Column("kind", types["review_kind"], nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("one_thing", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("source_query", sa.String(length=500), nullable=True),
        sa.Column("status", types["review_status"], nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["target_id"], ["seo_targets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_seo_reviews_target_id", "seo_reviews", ["target_id"], unique=False)
    op.create_index("ix_seo_reviews_url", "seo_reviews", ["url"], unique=False)
    op.create_index("ix_seo_reviews_domain", "seo_reviews", ["domain"], unique=False)
    op.create_index("ix_seo_reviews_brand", "seo_reviews", ["brand"], unique=False)
    op.create_index("ix_seo_reviews_kind", "seo_reviews", ["kind"], unique=False)
    op.create_index("ix_seo_reviews_status", "seo_reviews", ["status"], unique=False)

    op.create_table(
        "seo_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("review_id", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("brand", types["brand"], nullable=False),
        sa.Column("kind", types["plan_kind"], nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("one_thing", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("tasks", sa.JSON(), nullable=True),
        sa.Column("status", types["plan_status"], nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["target_id"], ["seo_targets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["review_id"], ["seo_reviews.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_seo_plans_target_id", "seo_plans", ["target_id"], unique=False)
    op.create_index("ix_seo_plans_review_id", "seo_plans", ["review_id"], unique=False)
    op.create_index("ix_seo_plans_url", "seo_plans", ["url"], unique=False)
    op.create_index("ix_seo_plans_domain", "seo_plans", ["domain"], unique=False)
    op.create_index("ix_seo_plans_brand", "seo_plans", ["brand"], unique=False)
    op.create_index("ix_seo_plans_kind", "seo_plans", ["kind"], unique=False)
    op.create_index("ix_seo_plans_status", "seo_plans", ["status"], unique=False)


def downgrade() -> None:
    for name in (
        "ix_seo_plans_status",
        "ix_seo_plans_kind",
        "ix_seo_plans_brand",
        "ix_seo_plans_domain",
        "ix_seo_plans_url",
        "ix_seo_plans_review_id",
        "ix_seo_plans_target_id",
    ):
        op.drop_index(name, table_name="seo_plans")
    op.drop_table("seo_plans")
    for name in (
        "ix_seo_reviews_status",
        "ix_seo_reviews_kind",
        "ix_seo_reviews_brand",
        "ix_seo_reviews_domain",
        "ix_seo_reviews_url",
        "ix_seo_reviews_target_id",
    ):
        op.drop_index(name, table_name="seo_reviews")
    op.drop_table("seo_reviews")
    for name in (
        "ix_seo_queries_status_brand_id",
        "ix_seo_queries_status",
        "ix_seo_queries_target_id",
        "ix_seo_queries_kind",
        "ix_seo_queries_brand",
    ):
        op.drop_index(name, table_name="seo_queries")
    op.drop_table("seo_queries")
    for name in (
        "ix_seo_targets_next_review_at",
        "ix_seo_targets_role",
        "ix_seo_targets_brand",
        "ix_seo_targets_domain",
        "ix_seo_targets_url",
    ):
        op.drop_index(name, table_name="seo_targets")
    op.drop_table("seo_targets")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum in reversed(_PG_ENUMS):
            enum.drop(bind, checkfirst=True)
    else:
        for *_, name in reversed(_SQLITE_ENUMS):
            sa.Enum(name=name).drop(bind, checkfirst=True)
