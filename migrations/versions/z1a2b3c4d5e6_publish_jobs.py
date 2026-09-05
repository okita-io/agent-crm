"""Publish jobs and social accounts for the publisher worker.

Revision ID: z1a2b3c4d5e6
Revises: y0z1a2b3c4d5
Create Date: 2026-09-04 15:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "z1a2b3c4d5e6"
down_revision: str | None = "y0z1a2b3c4d5"
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

_DRAFT_STATUS_VALUES = (
    "draft",
    "review",
    "approved",
    "scheduled",
    "posted",
    "rejected",
)
_NEW_DRAFT_STATUSES = ("approved", "scheduled", "posted")

platform_enum = postgresql.ENUM(
    "reddit",
    "x",
    "linkedin",
    "instagram",
    "threads",
    "other",
    name="socialplatform",
    create_type=False,
)

source_kind_enum = postgresql.ENUM(
    "engagement_draft",
    "content_package",
    name="publishsourcekind",
    create_type=False,
)

job_status_enum = postgresql.ENUM(
    "scheduled",
    "sending",
    "posted",
    "failed",
    "cancelled",
    name="publishjobstatus",
    create_type=False,
)


def _ensure_enums(bind) -> None:
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for value in _NEW_DRAFT_STATUSES:
                op.execute(
                    f"ALTER TYPE engagementdraftstatus ADD VALUE IF NOT EXISTS '{value}'"
                )
        platform_enum.create(bind, checkfirst=True)
        source_kind_enum.create(bind, checkfirst=True)
        job_status_enum.create(bind, checkfirst=True)
        return
    sa.Enum(*_DRAFT_STATUS_VALUES, name="engagementdraftstatus").create(
        bind, checkfirst=True
    )
    sa.Enum(
        "reddit",
        "x",
        "linkedin",
        "instagram",
        "threads",
        "other",
        name="socialplatform",
    ).create(bind, checkfirst=True)
    sa.Enum(
        "engagement_draft",
        "content_package",
        name="publishsourcekind",
    ).create(bind, checkfirst=True)
    sa.Enum(
        "scheduled",
        "sending",
        "posted",
        "failed",
        "cancelled",
        name="publishjobstatus",
    ).create(bind, checkfirst=True)


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_enums(bind)

    brand_col = brand_enum if bind.dialect.name == "postgresql" else sa.String(length=32)
    platform_col = (
        platform_enum if bind.dialect.name == "postgresql" else sa.String(length=32)
    )
    source_col = (
        source_kind_enum if bind.dialect.name == "postgresql" else sa.String(length=32)
    )
    job_status_col = (
        job_status_enum if bind.dialect.name == "postgresql" else sa.String(length=32)
    )

    op.create_table(
        "social_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("brand", brand_col, nullable=False),
        sa.Column("platform", platform_col, nullable=False),
        sa.Column("handle", sa.String(length=128), nullable=False),
        sa.Column("postiz_integration_id", sa.String(length=128), nullable=True),
        sa.Column("credential_key", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("daily_cap", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "min_interval_minutes", sa.Integer(), nullable=False, server_default="240"
        ),
        sa.Column("last_posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "brand",
            "platform",
            "handle",
            name="uq_social_accounts_brand_platform_handle",
        ),
    )
    op.create_index(
        op.f("ix_social_accounts_brand"), "social_accounts", ["brand"], unique=False
    )
    op.create_index(
        op.f("ix_social_accounts_platform"),
        "social_accounts",
        ["platform"],
        unique=False,
    )
    op.create_index(
        op.f("ix_social_accounts_enabled"),
        "social_accounts",
        ["enabled"],
        unique=False,
    )

    op.create_table(
        "publish_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_kind", source_col, nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("brand", brand_col, nullable=False),
        sa.Column("platform", platform_col, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("target_url", sa.String(length=2048), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", job_status_col, nullable=False, server_default="scheduled"
        ),
        sa.Column("posted_url", sa.String(length=2048), nullable=True),
        sa.Column("platform_post_id", sa.String(length=256), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "pete_override", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["social_accounts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_publish_jobs_source_kind"),
        "publish_jobs",
        ["source_kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_publish_jobs_source_id"), "publish_jobs", ["source_id"], unique=False
    )
    op.create_index(
        op.f("ix_publish_jobs_brand"), "publish_jobs", ["brand"], unique=False
    )
    op.create_index(
        op.f("ix_publish_jobs_platform"), "publish_jobs", ["platform"], unique=False
    )
    op.create_index(
        op.f("ix_publish_jobs_account_id"),
        "publish_jobs",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_publish_jobs_scheduled_at"),
        "publish_jobs",
        ["scheduled_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_publish_jobs_status"), "publish_jobs", ["status"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_publish_jobs_status"), table_name="publish_jobs")
    op.drop_index(op.f("ix_publish_jobs_scheduled_at"), table_name="publish_jobs")
    op.drop_index(op.f("ix_publish_jobs_account_id"), table_name="publish_jobs")
    op.drop_index(op.f("ix_publish_jobs_platform"), table_name="publish_jobs")
    op.drop_index(op.f("ix_publish_jobs_brand"), table_name="publish_jobs")
    op.drop_index(op.f("ix_publish_jobs_source_id"), table_name="publish_jobs")
    op.drop_index(op.f("ix_publish_jobs_source_kind"), table_name="publish_jobs")
    op.drop_table("publish_jobs")

    op.drop_index(op.f("ix_social_accounts_enabled"), table_name="social_accounts")
    op.drop_index(op.f("ix_social_accounts_platform"), table_name="social_accounts")
    op.drop_index(op.f("ix_social_accounts_brand"), table_name="social_accounts")
    op.drop_table("social_accounts")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        job_status_enum.drop(bind, checkfirst=True)
        source_kind_enum.drop(bind, checkfirst=True)
        platform_enum.drop(bind, checkfirst=True)
    else:
        sa.Enum(name="publishjobstatus").drop(bind, checkfirst=True)
        sa.Enum(name="publishsourcekind").drop(bind, checkfirst=True)
        sa.Enum(name="socialplatform").drop(bind, checkfirst=True)
