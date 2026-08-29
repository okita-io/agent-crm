"""Queue review, page-type, and ingest deny-list.

Revision ID: s4t5u6v7w8x9
Revises: r3s4t5u6v7w8
Create Date: 2026-08-29 09:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s4t5u6v7w8x9"
down_revision: str | None = "r3s4t5u6v7w8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_QUEUE_STATUSES = ("pending_review", "rejected")
_SOURCE_AGENTS = ("engagement-loop", "seo-loop", "queue-review")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for value in _QUEUE_STATUSES:
                op.execute(
                    f"ALTER TYPE huntquerystatus ADD VALUE IF NOT EXISTS '{value}'"
                )
                op.execute(
                    f"ALTER TYPE researchquerystatus ADD VALUE IF NOT EXISTS '{value}'"
                )
                op.execute(
                    f"ALTER TYPE engagementquerystatus ADD VALUE IF NOT EXISTS '{value}'"
                )
            for value in _SOURCE_AGENTS:
                op.execute(
                    f"ALTER TYPE improvementsourceagent ADD VALUE IF NOT EXISTS '{value}'"
                )
        huntpagetype = sa.Enum(
            "outlet_article",
            "outlet_section",
            "social_profile",
            "community",
            "docs",
            "ad_page",
            "other",
            name="huntpagetype",
        )
        huntpagetype.create(bind, checkfirst=True)
    else:
        huntpagetype = sa.Enum(
            "outlet_article",
            "outlet_section",
            "social_profile",
            "community",
            "docs",
            "ad_page",
            "other",
            name="huntpagetype",
        )

    op.add_column(
        "hunt_resources",
        sa.Column(
            "page_type",
            huntpagetype,
            nullable=False,
            server_default="other",
        ),
    )
    op.add_column(
        "hunt_resources",
        sa.Column("domain_class", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_hunt_resources_page_type", "hunt_resources", ["page_type"])
    op.create_index(
        "ix_hunt_resources_domain_class", "hunt_resources", ["domain_class"]
    )

    if bind.dialect.name == "postgresql":
        op.drop_constraint("research_findings_url_key", "research_findings", type_="unique")
    op.create_unique_constraint(
        "uq_research_findings_url_brand",
        "research_findings",
        ["url", "brand"],
    )

    for table in ("hunt_queries", "research_queries", "engagement_queries"):
        # Escape colons so SQLAlchemy text() does not treat ``:seed_pack`` as a bind.
        op.execute(
            sa.text(
                f"""
                UPDATE {table}
                SET status = 'pending_review'
                WHERE status = 'pending'
                  AND origin NOT IN (
                    'seed', 'seed_pack', 'explicit',
                    'marketing\\:seed', 'influencer\\:seed', 'user\\:seed'
                  )
                  AND origin NOT LIKE '%\\:seed_pack'
                  AND origin NOT LIKE 'seed%'
                  AND origin NOT LIKE 'venue\\:%'
                """
            )
        )


def downgrade() -> None:
    op.drop_constraint(
        "uq_research_findings_url_brand", "research_findings", type_="unique"
    )
    op.drop_index("ix_hunt_resources_domain_class", table_name="hunt_resources")
    op.drop_index("ix_hunt_resources_page_type", table_name="hunt_resources")
    op.drop_column("hunt_resources", "domain_class")
    op.drop_column("hunt_resources", "page_type")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="huntpagetype").drop(bind, checkfirst=True)
