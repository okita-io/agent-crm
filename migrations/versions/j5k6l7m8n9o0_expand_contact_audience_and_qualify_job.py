"""Expand contactaudience enum and add qualify_contact job kind.

Revision ID: j5k6l7m8n9o0
Revises: i4j5k6l7m8n9
Create Date: 2026-08-27 22:10:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "j5k6l7m8n9o0"
down_revision: str | None = "i4j5k6l7m8n9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE contactaudience ADD VALUE IF NOT EXISTS 'end_user'")
        op.execute("ALTER TYPE contactaudience ADD VALUE IF NOT EXISTS 'b2b'")
        op.execute("ALTER TYPE contactaudience ADD VALUE IF NOT EXISTS 'client'")
        op.execute("ALTER TYPE agentjobkind ADD VALUE IF NOT EXISTS 'qualify_contact'")


def downgrade() -> None:
    # Postgres enum values cannot be removed safely; no-op downgrade.
    pass
