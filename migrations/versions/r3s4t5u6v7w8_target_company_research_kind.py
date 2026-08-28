"""Add target_company research finding kind.

Revision ID: r3s4t5u6v7w8
Revises: q2r3s4t5u6v7
Create Date: 2026-08-28 16:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "r3s4t5u6v7w8"
down_revision: str | None = "q2r3s4t5u6v7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE researchfindingkind ADD VALUE IF NOT EXISTS 'target_company'"
        )


def downgrade() -> None:
    # Postgres enum values cannot be removed safely; leave target_company in place.
    pass
