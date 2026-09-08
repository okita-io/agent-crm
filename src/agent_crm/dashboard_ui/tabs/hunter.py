"""Dashboard UI module: tabs.hunter."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import streamlit as st

from agent_crm.enums import AgencyRequestStatus, AgentStatus, Brand, ContactAudience, ContactVerificationStatus, HuntQueryStatus, HuntResourceKind, LeadSource, ResearchFindingKind, SeoPlanKind, SeoReviewKind, Stage
from agent_crm.hunt.feedback import parse_community_notes
from agent_crm.hunt.store import HuntStore

from agent_crm.dashboard_ui.common import (
    _export_filename,
    _render_catalog_growth,
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


def _query_inspector_rows(
    *,
    brand: Brand | None,
    status: HuntQueryStatus | None,
    origin_prefix: str | None,
    q: str | None,
    limit: int | None = 200,
) -> pd.DataFrame:
    store = HuntStore()
    rows = store.list_queries(
        brand=brand,
        status=status,
        origin_prefix=origin_prefix or None,
        q=q or None,
        limit=limit or 200,
        drain_order=status in {HuntQueryStatus.PENDING, HuntQueryStatus.PENDING_REVIEW, HuntQueryStatus.RUNNING},
    )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "id": row.id,
                "query": row.query,
                "origin": row.origin,
                "brand": row.brand.value,
                "priority": row.priority,
                "status": row.status.value,
                "error": row.error_message or "",
                "updated": row.updated_at,
            }
            for row in rows
        ]
    )


def _render_hunt_query_inspector() -> None:
    st.subheader("Queue inspector")
    st.caption(
        "Full hunt_queries list in drain order. Toss pending/review/failed terms here, "
        "or open the same inspector from the Vite Hunter queries box."
    )
    store = HuntStore()
    counts = store.queue_status()["by_status"]
    cols = st.columns(5)
    cols[0].metric("Pending", counts.get("pending", 0))
    cols[1].metric("Running", counts.get("running", 0))
    cols[2].metric("Review", counts.get("pending_review", 0))
    cols[3].metric("Failed", counts.get("failed", 0))
    cols[4].metric("Tossed", counts.get("rejected", 0))

    filters = st.columns(4)
    status_choice = filters[0].selectbox(
        "Status",
        options=["pending", "running", "pending_review", "failed", "completed", "rejected", "all"],
        key="hunt_inspector_status",
    )
    brand_choice = filters[1].selectbox(
        "Brand",
        options=["all"] + [b.value for b in Brand if b != Brand.UNASSIGNED],
        key="hunt_inspector_brand",
    )
    origin_prefix = filters[2].text_input(
        "Origin prefix",
        value="",
        placeholder="branch",
        key="hunt_inspector_origin",
    )
    search = filters[3].text_input("Search", value="", key="hunt_inspector_q")
    status = None if status_choice == "all" else HuntQueryStatus(status_choice)
    brand = None if brand_choice == "all" else Brand(brand_choice)
    df = _query_inspector_rows(
        brand=brand,
        status=status,
        origin_prefix=origin_prefix.strip() or None,
        q=search.strip() or None,
        limit=200,
    )
    matching = store.count_queries(
        brand=brand,
        status=status,
        origin_prefix=origin_prefix.strip() or None,
        q=search.strip() or None,
    )
    st.caption(f"{matching} matching (showing {len(df)})")
    if df.empty:
        st.info("No hunt queries match this filter.")
        return
    st.dataframe(df, use_container_width=True, hide_index=True)
    tossable = status in {
        HuntQueryStatus.PENDING,
        HuntQueryStatus.PENDING_REVIEW,
        HuntQueryStatus.FAILED,
    }
    selected_ids = st.multiselect(
        "Toss selected ids",
        options=df["id"].tolist(),
        key="hunt_inspector_ids",
    )
    actions = st.columns(3)
    if actions[0].button("Toss selected", disabled=not selected_ids, key="hunt_toss_selected"):
        tossed = store.reject_queries(selected_ids, "operator toss")
        st.success(f"Tossed {tossed} queries.")
        st.rerun()
    matching_ok = tossable and (brand is not None or origin_prefix.strip() or search.strip())
    if actions[1].button(
        "Toss matching filter",
        disabled=not matching_ok,
        key="hunt_toss_matching",
    ):
        tossed = store.reject_matching(
            status=status,
            brand=brand,
            origin_prefix=origin_prefix.strip() or None,
            q=search.strip() or None,
            reason="operator toss",
        )
        st.success(f"Tossed {tossed} matching queries.")
        st.rerun()
    retry_ids = df.loc[df["status"] == "failed", "id"].tolist() if "status" in df else []
    if actions[2].button("Retry visible failed", disabled=not retry_ids, key="hunt_retry_failed"):
        retried = 0
        for query_id in retry_ids:
            row = store.retry_query(int(query_id))
            if row is not None and row.status != HuntQueryStatus.FAILED:
                retried += 1
        st.success(f"Re-queued {retried} failed queries.")
        st.rerun()

    clearable = (
        int(counts.get("pending", 0))
        + int(counts.get("pending_review", 0))
        + int(counts.get("failed", 0))
    )
    if "hunt_clear_confirm" not in st.session_state:
        st.session_state.hunt_clear_confirm = False
    if st.button("Clear queue", disabled=clearable == 0, key="hunt_clear_queue"):
        st.session_state.hunt_clear_confirm = True
    if st.session_state.hunt_clear_confirm:
        st.warning(
            f"Clear hunter queue? This tosses {clearable} pending, review, and failed queries. "
            "The running query and completed history stay. Tossed seed terms will not re-enqueue."
        )
        confirm_cols = st.columns(2)
        if confirm_cols[0].button("Cancel", key="hunt_clear_cancel"):
            st.session_state.hunt_clear_confirm = False
            st.rerun()
        if confirm_cols[1].button("OK", key="hunt_clear_ok"):
            tossed = store.clear_queue(reason="operator clear")
            st.session_state.hunt_clear_confirm = False
            st.success(f"Cleared {tossed} queries.")
            st.rerun()


def _render_hunter_tab(refresh_seconds: int) -> None:
    try:
        fragment = st.fragment(run_every=timedelta(seconds=refresh_seconds))
    except TypeError:
        fragment = st.fragment

    @fragment
    def _hunter_live() -> None:
        _render_hunt_loop_status(refresh_seconds=refresh_seconds)
        _render_catalog_growth(compact=True)

    _hunter_live()

    _render_hunt_query_inspector()

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

