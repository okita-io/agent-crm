"""Add people-enrichment fields to contact_profiles.

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-08-26 21:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: str | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("contact_profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("title", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("organization", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("location", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("bio", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("enrichment", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("contact_profiles", schema=None) as batch_op:
        batch_op.drop_column("enrichment")
        batch_op.drop_column("bio")
        batch_op.drop_column("location")
        batch_op.drop_column("organization")
        batch_op.drop_column("title")
