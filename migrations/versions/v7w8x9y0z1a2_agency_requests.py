"""Operator command queue for the orchestrator Command tab.

Revision ID: v7w8x9y0z1a2
Revises: u6v7w8x9y0z1
Create Date: 2026-09-01 16:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "v7w8x9y0z1a2"
down_revision: str | None = "u6v7w8x9y0z1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

agency_request_status_enum = postgresql.ENUM(
    "pending",
    "processing",
    "completed",
    "failed",
    name="agencyrequeststatus",
    create_type=False,
)


def _ensure_status_enum(bind) -> None:
    if bind.dialect.name == "postgresql":
        agency_request_status_enum.create(bind, checkfirst=True)
        return
    sa.Enum(
        "pending",
        "processing",
        "completed",
        "failed",
        name="agencyrequeststatus",
    ).create(bind, checkfirst=True)


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_status_enum(bind)

    status_type = agency_request_status_enum
    if bind.dialect.name != "postgresql":
        status_type = sa.Enum(
            "pending",
            "processing",
            "completed",
            "failed",
            name="agencyrequeststatus",
        )

    op.create_table(
        "agency_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", status_type, nullable=False, server_default="pending"),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("actions", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("agency_requests")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        agency_request_status_enum.drop(bind, checkfirst=True)
    else:
        sa.Enum(name="agencyrequeststatus").drop(bind, checkfirst=True)
