"""Add contact_profiles table and CONTACT lead source.

Revision ID: c5d6e7f8a9b0
Revises: b4c9d3e82f15
Create Date: 2026-08-25 22:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c5d6e7f8a9b0"
down_revision: str | None = "b4c9d3e82f15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

brand_enum = postgresql.ENUM(
    "MIDNIGHTSATIN",
    "CELESTIAL_NEXUS",
    "HEYBUDDY",
    "UNASSIGNED",
    name="brand",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE leadsource ADD VALUE IF NOT EXISTS 'CONTACT'")

    op.create_table(
        "contact_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("brand", brand_enum, nullable=False),
        sa.Column("socials", sa.JSON(), nullable=True),
        sa.Column("source_urls", sa.JSON(), nullable=True),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_contact_profiles_email"),
    )
    with op.batch_alter_table("contact_profiles", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_contact_profiles_brand"), ["brand"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_contact_profiles_lead_id"), ["lead_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("contact_profiles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_contact_profiles_lead_id"))
        batch_op.drop_index(batch_op.f("ix_contact_profiles_brand"))
    op.drop_table("contact_profiles")
