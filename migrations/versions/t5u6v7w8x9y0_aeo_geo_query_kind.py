"""Add AEO/GEO query kind to SEO job queue.

Revision ID: t5u6v7w8x9y0
Revises: s4t5u6v7w8x9
Create Date: 2026-08-31 21:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "t5u6v7w8x9y0"
down_revision: str | None = "s4t5u6v7w8x9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                "ALTER TYPE seoquerykind ADD VALUE IF NOT EXISTS 'aeo_geo'"
            )
            op.execute(
                "ALTER TYPE improvementsourceagent ADD VALUE IF NOT EXISTS 'aeo-geo-loop'"
            )


def downgrade() -> None:
    # Postgres enum values cannot be removed safely; no-op.
    pass
