"""Dashboard UI module: tabs.hunter."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import streamlit as st

from agent_crm.enums import AgencyRequestStatus, AgentStatus, Brand, ContactAudience, ContactVerificationStatus, HuntResourceKind, LeadSource, ResearchFindingKind, SeoPlanKind, SeoReviewKind, Stage
from agent_crm.hunt.feedback import parse_community_notes
from agent_crm.hunt.store import HuntStore

from agent_crm.dashboard_ui.common import (
    _export_filename,
    _render_full_csv_export,
    _render_hunt_loop_status,
)

def _resource_rows(brand: Brand | None, *, limit: int | None = 500) -> pd.DataFrame:
    resources = HuntStore().list_resources(brand=brand, limit=limit)
    if not resources:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "domain": r.domain,
                "title": r.title,
                "kind": r.kind.value,
                "brand": r.brand.value,
                "hits": r.hit_count,
                "found_via": (r.found_via_query or "")[:80] if limit is not None else (r.found_via_query or ""),
                "url": r.url,
                "last_seen": r.last_seen,
            }
            for r in resources
        ]
    )


def _community_resource_rows(
    brand: Brand | None, *, limit: int | None = 200
) -> pd.DataFrame:
    community_kinds = (
        HuntResourceKind.COMMUNITY,
        HuntResourceKind.FORUM,
        HuntResourceKind.SOCIAL,
    )
    rows = HuntStore().list_resources(brand=brand, kinds=community_kinds, limit=limit)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "kind": row.kind.value,
                "title": row.title,
                "domain": row.domain,
                "slug": (parse_community_notes(row.notes) or {}).get("slug"),
                "brand": row.brand.value,
                "hits": row.hit_count,
                "engagement": row.engagement_score,
                "url": row.url,
                "last_seen": row.last_seen,
            }
            for row in rows
        ]
    )


def _derived_query_rows(brand: Brand | None, *, limit: int | None = 200) -> pd.DataFrame:
    rows = HuntStore().list_feedback_queries(brand=brand, limit=limit)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "origin": row.origin,
                "query": row.query,
                "status": row.status.value,
                "brand": row.brand.value,
                "created": row.created_at,
            }
            for row in rows
        ]
    )


def _render_hunter_tab(refresh_seconds: int) -> None:
    try:
        fragment = st.fragment(run_every=timedelta(seconds=refresh_seconds))
    except TypeError:
        fragment = st.fragment

    @fragment
    def _hunter_live() -> None:
        _render_hunt_loop_status(refresh_seconds=refresh_seconds)

    _hunter_live()

    st.subheader("Hunter resources")
    status = HuntStore().queue_status()
    store = HuntStore()

    cols = st.columns(5)
    cols[0].metric("Pending queries", status["pending"])
    cols[1].metric("Total resources", status["total_resources"])
    cols[2].metric("Completed queries", status["by_status"].get("completed", 0))
    feedback_queries = store.list_feedback_queries(limit=500)
    community_pending = sum(
        1 for row in feedback_queries if row.origin.startswith("community:")
    )
    person_pending = sum(1 for row in feedback_queries if row.origin.startswith("person:"))
    cols[3].metric("Community terms queued", community_pending)
    cols[4].metric("Person terms queued", person_pending)

    brand_filter = st.selectbox(
        "Brand filter",
        options=["all"] + [b.value for b in Brand if b != Brand.UNASSIGNED],
        key="resource_brand",
    )
    brand = None if brand_filter == "all" else Brand(brand_filter)

    st.subheader("Communities & forums")
    communities_df = _community_resource_rows(brand, limit=200)
    if communities_df.empty:
        st.info("No community resources catalogued yet.")
    else:
        _render_full_csv_export(
            key="hunter_communities",
            filename=_export_filename(
                "hunter-communities", brand.value if brand else None
            ),
            fetch_all=lambda: _community_resource_rows(brand, limit=None),
            preview_count=len(communities_df),
            preview_cap=200,
            filter_key=brand_filter,
        )
        st.dataframe(communities_df, use_container_width=True, hide_index=True)

    st.subheader("Derived hunt queries")
    st.caption(
        "Deterministic follow-ups from discovered communities and extracted contact names. "
        "Inspect `origin` on `hunt_queries` (prefix `community:` or `person:`); "
        "`GET /hunt/queue` reports aggregate pending counts."
    )
    derived_df = _derived_query_rows(brand, limit=200)
    if derived_df.empty:
        st.info("No community/person feedback queries yet.")
    else:
        _render_full_csv_export(
            key="hunter_derived_queries",
            filename=_export_filename(
                "hunter-derived-queries", brand.value if brand else None
            ),
            fetch_all=lambda: _derived_query_rows(brand, limit=None),
            preview_count=len(derived_df),
            preview_cap=200,
            filter_key=brand_filter,
        )
        st.dataframe(derived_df, use_container_width=True, hide_index=True)

    st.subheader("All hunter resources")
    df = _resource_rows(brand, limit=500)
    if df.empty:
        st.info("No hunter resources yet. Run `agent-crm hunt-loop --brand midnightsatin`.")
    else:
        _render_full_csv_export(
            key="hunter_resources",
            filename=_export_filename(
                "hunter-resources", brand.value if brand else None
            ),
            fetch_all=lambda: _resource_rows(brand, limit=None),
            preview_count=len(df),
            preview_cap=500,
            filter_key=brand_filter,
        )
        st.dataframe(df, use_container_width=True, hide_index=True)

