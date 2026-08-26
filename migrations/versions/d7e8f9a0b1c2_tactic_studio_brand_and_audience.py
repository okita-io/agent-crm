"""Add tactic.studio brand, contact audiences, and hunt query priority.

Revision ID: d7e8f9a0b1c2
Revises: c5d6e7f8a9b0
Create Date: 2026-08-26 16:50:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7e8f9a0b1c2"
down_revision: str | None = "c5d6e7f8a9b0"
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

contact_audience_enum = postgresql.ENUM(
    "marketing",
    "influencer",
    "user",
    name="contactaudience",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE brand ADD VALUE IF NOT EXISTS 'TACTIC_STUDIO'")
        contact_audience_enum.create(bind, checkfirst=True)

    audience_type = sa.Enum(
        "marketing",
        "influencer",
        "user",
        name="contactaudience",
    )
    if bind.dialect.name != "postgresql":
        audience_type.create(bind, checkfirst=True)

    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "audience",
                audience_type,
                nullable=True,
            )
        )
        batch_op.create_index(batch_op.f("ix_leads_audience"), ["audience"], unique=False)

    with op.batch_alter_table("contact_profiles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "audience",
                audience_type,
                nullable=True,
            )
        )
        batch_op.create_index(
            batch_op.f("ix_contact_profiles_audience"), ["audience"], unique=False
        )

    with op.batch_alter_table("hunt_queries", schema=None) as batch_op:
        batch_op.add_column(sa.Column("priority", sa.Integer(), nullable=False, server_default="30"))
        batch_op.create_index(
            "ix_hunt_queries_status_priority_id",
            ["status", "priority", "id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("hunt_queries", schema=None) as batch_op:
        batch_op.drop_index("ix_hunt_queries_status_priority_id")
        batch_op.drop_column("priority")

    with op.batch_alter_table("contact_profiles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_contact_profiles_audience"))
        batch_op.drop_column("audience")

    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_leads_audience"))
        batch_op.drop_column("audience")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS contactaudience")
