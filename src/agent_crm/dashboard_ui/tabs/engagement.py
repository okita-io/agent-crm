"""Dashboard UI module: tabs.engagement."""
from __future__ import annotations


import pandas as pd
import streamlit as st

from agent_crm.engagement_query_store import EngagementQueryStore
from agent_crm.engagement_store import count_drafts, count_threads, list_drafts, list_threads
from agent_crm.enums import AgencyRequestStatus, AgentStatus, Brand, ContactAudience, ContactVerificationStatus, HuntResourceKind, LeadSource, ResearchFindingKind, SeoPlanKind, SeoReviewKind, Stage

from agent_crm.dashboard_ui.common import (
    _clip,
    _export_filename,
    _render_full_csv_export,
)

def _engagement_thread_rows(
    brand: Brand | None, *, limit: int | None = 200
) -> pd.DataFrame:
    threads = list_threads(brand=brand, limit=limit)
    if not threads:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "score": row.popularity_score,
                "comments": row.comment_count,
                "status": row.status.value,
                "title": row.title,
                "platform": row.platform,
                "brand": row.brand.value,
                "url": row.url,
                "trends": ", ".join(row.trend_keywords or []),
                "last_scanned": row.last_scanned_at,
            }
            for row in threads
        ]
    )


def _engagement_draft_rows(
    brand: Brand | None, *, limit: int | None = 200, truncate: bool = True
) -> pd.DataFrame:
    drafts = list_drafts(brand=brand, limit=limit)
    if not drafts:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "status": row.status.value,
                "brand": row.brand.value,
                "angle": row.product_angle,
                "draft": _clip(row.draft_text, 400, truncate=truncate),
                "thread_id": row.thread_id,
                "updated": row.updated_at,
            }
            for row in drafts
        ]
    )


def _render_engagement_tab() -> None:
    st.subheader("Agent engagement")
    st.caption(
        "High-traffic forums and popular threads catalogued for later comment drafts. "
        "The query queue is append-only: scraped pages enqueue new community/thread searches. "
        "This stack never posts — drafts stay here for human review."
    )

    queue = EngagementQueryStore().queue_status()
    brand_filter = st.selectbox(
        "Brand filter",
        options=["all"] + [b.value for b in Brand if b != Brand.UNASSIGNED],
        key="engagement_brand",
    )
    brand = None if brand_filter == "all" else Brand(brand_filter)

    qcols = st.columns(4)
    qcols[0].metric("Queued terms (total)", queue.get("total", 0))
    qcols[1].metric("Pending", queue.get("pending", 0))
    qcols[2].metric("Catalogued threads", count_threads(brand=brand))
    qcols[3].metric("Comment drafts", count_drafts(brand=brand))

    st.subheader("Popular threads")
    threads_df = _engagement_thread_rows(brand, limit=200)
    if threads_df.empty:
        st.info(
            "No threads yet. The hunter catalogs forums, then `agent-crm engagement-loop` "
            "scans them for popular posts."
        )
    else:
        _render_full_csv_export(
            key="engagement_threads",
            filename=_export_filename(
                "engagement-threads", brand.value if brand else None
            ),
            fetch_all=lambda: _engagement_thread_rows(brand, limit=None),
            preview_count=len(threads_df),
            preview_cap=200,
            filter_key=brand_filter,
        )
        st.dataframe(threads_df, use_container_width=True, hide_index=True)

    st.subheader("Draft replies (not posted)")
    drafts_df = _engagement_draft_rows(brand, limit=200)
    if drafts_df.empty:
        st.info("No drafts yet. Engagement loop writes drafts when a thread is popular enough.")
    else:
        _render_full_csv_export(
            key="engagement_drafts",
            filename=_export_filename(
                "engagement-drafts", brand.value if brand else None
            ),
            fetch_all=lambda: _engagement_draft_rows(
                brand, limit=None, truncate=False
            ),
            preview_count=len(drafts_df),
            preview_cap=200,
            filter_key=brand_filter,
        )
        st.dataframe(drafts_df, use_container_width=True, hide_index=True)

