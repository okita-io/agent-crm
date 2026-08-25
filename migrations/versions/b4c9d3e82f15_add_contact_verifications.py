"""Add contact verification table for lead verifier agent.

Revision ID: b4c9d3e82f15
Revises: b2c3d4e5f6a7
Create Date: 2026-08-25 21:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4c9d3e82f15"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contact_verifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("contact", sa.String(length=2048), nullable=False),
        sa.Column(
            "contact_kind",
            sa.Enum("email", "url", name="contactkind"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("valid", "invalid", "risky", "unknown", name="contactverificationstatus"),
            nullable=False,
        ),
        sa.Column("reasons", sa.JSON(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dns_summary", sa.JSON(), nullable=True),
        sa.Column("mx_summary", sa.JSON(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lead_id", "contact", name="uq_lead_contact"),
    )
    with op.batch_alter_table("contact_verifications", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_contact_verifications_lead_id"), ["lead_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("contact_verifications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_contact_verifications_lead_id"))
    op.drop_table("contact_verifications")
    op.execute("DROP TYPE IF EXISTS contactverificationstatus")
    op.execute("DROP TYPE IF EXISTS contactkind")
