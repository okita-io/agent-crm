"""Add ad_placement research finding kind.

Revision ID: g2h3i4j5k6l7
Revises: f1a2b3c4d5e6
Create Date: 2026-08-26 22:35:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "g2h3i4j5k6l7"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE researchfindingkind ADD VALUE IF NOT EXISTS 'ad_placement'"
        )


def downgrade() -> None:
    # Postgres enum values cannot be removed safely; leave ad_placement in place.
    pass
