"""Dashboard UI module: tabs.research."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from agent_crm.enums import AgencyRequestStatus, AgentStatus, Brand, ContactAudience, ContactVerificationStatus, HuntResourceKind, LeadSource, ResearchFindingKind, SeoPlanKind, SeoReviewKind, Stage
from agent_crm.research_query_store import ResearchQueryStore
from agent_crm.research_store import list_findings

from agent_crm.dashboard_ui.common import (
    _clip,
    _export_filename,
    _render_full_csv_export,
)

def _research_finding_rows(
    *,
    brand: Brand | None,
    kind: ResearchFindingKind | None,
    limit: int | None = 500,
    truncate: bool = True,
) -> pd.DataFrame:
    findings = list_findings(brand=brand, kind=kind, limit=limit)
    if not findings:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "id": row.id,
                "brand": row.brand.value,
                "kind": row.kind.value,
                "domain": row.domain,
                "title": row.title,
                "url": row.url,
                "summary": _clip(row.summary, 240, truncate=truncate),
                "source query": row.source_query,
                "extra": json.dumps(row.extra) if row.extra else None,
                "last seen": row.last_seen_at,
            }
            for row in findings
        ]
    )


def _render_research_tab() -> None:
    st.subheader("Research findings")
    st.caption(
        "Competitor, nonprofit, ad-placement, and retail target-company prospecting. "
        "The query queue is append-only: SearXNG/Firecrawl pages enqueue new search "
        "terms and rows are never deleted. tactic.studio target-company findings "
        "enqueue hunter searches for VPs of marketing/sales and marketing managers."
    )

    queue = ResearchQueryStore().queue_status()
    qcols = st.columns(4)
    qcols[0].metric("Queued terms (total)", queue.get("total", 0))
    qcols[1].metric("Pending", queue.get("pending", 0))
    qcols[2].metric("Completed", queue.get("completed", 0))
    qcols[3].metric("Failed", queue.get("failed", 0))

    brand_filter = st.selectbox(
        "Brand filter",
        options=["all", "celestial-nexus", "midnightsatin", "heybuddy", "tactic-studio"],
        index=0,
        key="research_brand",
    )
    kind_filter = st.selectbox(
        "Kind filter",
        options=["all", "competitor", "nonprofit", "ad_placement", "target_company", "other"],
        index=0,
        key="research_kind",
    )

    brand = Brand(brand_filter) if brand_filter != "all" else None
    kind = ResearchFindingKind(kind_filter) if kind_filter != "all" else None
    findings_df = _research_finding_rows(brand=brand, kind=kind, limit=500)

    if findings_df.empty:
        st.info(
            "No findings yet. Run `agent-crm research --brand celestial-nexus` "
            "or POST to /research."
        )
        return

    st.metric("Findings", len(findings_df))
    _render_full_csv_export(
        key="research_findings",
        filename=_export_filename(
            "research-findings",
            brand.value if brand else None,
            kind.value if kind else None,
        ),
        fetch_all=lambda: _research_finding_rows(
            brand=brand, kind=kind, limit=None, truncate=False
        ),
        preview_count=len(findings_df),
        preview_cap=500,
        filter_key=(brand_filter, kind_filter),
    )
    st.dataframe(findings_df, use_container_width=True, hide_index=True)

