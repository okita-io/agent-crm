"""Add contact_profiles.email_kind for SQL quality filters.

Revision ID: l7m8n9o0p1q2
Revises: k6l7m8n9o0p1
Create Date: 2026-08-27 23:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "l7m8n9o0p1q2"
down_revision: str | None = "k6l7m8n9o0p1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

email_kind_enum = postgresql.ENUM(
    "person",
    "role",
    "junk",
    name="contactemailkind",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        email_kind_enum.create(bind, checkfirst=True)
        op.add_column(
            "contact_profiles",
            sa.Column(
                "email_kind",
                email_kind_enum,
                nullable=False,
                server_default="person",
            ),
        )
    else:
        op.add_column(
            "contact_profiles",
            sa.Column(
                "email_kind",
                sa.Enum("person", "role", "junk", name="contactemailkind"),
                nullable=False,
                server_default="person",
            ),
        )
    op.create_index(
        "ix_contact_profiles_email_kind",
        "contact_profiles",
        ["email_kind"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_contact_profiles_email_kind", table_name="contact_profiles")
    op.drop_column("contact_profiles", "email_kind")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        email_kind_enum.drop(bind, checkfirst=True)
