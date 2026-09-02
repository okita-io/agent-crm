"""Dashboard UI module: tabs.aeo_geo."""
from __future__ import annotations


import pandas as pd
import streamlit as st

from agent_crm.enums import AgencyRequestStatus, AgentStatus, Brand, ContactAudience, ContactVerificationStatus, HuntResourceKind, LeadSource, ResearchFindingKind, SeoPlanKind, SeoReviewKind, Stage
from agent_crm.seo.store import count_plans, count_reviews, count_targets, list_plans, list_reviews, list_targets

from agent_crm.dashboard_ui.common import (
    _export_filename,
    _render_full_csv_export,
)
from agent_crm.dashboard_ui.tabs.seo import (
    _pick_seo_document,
    _seo_download_buttons,
    _seo_plan_rows,
    _seo_review_rows,
)

def _render_aeo_geo_tab() -> None:
    st.subheader("AEO / GEO documents")
    st.caption(
        "Answer-engine (AEO) and generative-engine (GEO) reviews and implementation plans. "
        "SEO = blue-link rank; AEO = extractable answers; GEO = chat citations and mentions. "
        "The agent scrapes with Firecrawl and writes markdown at least once a day — "
        "it never patches live pages or sends outreach. "
        "Download the open file as markdown, or export every file in the list as a zip."
    )

    brand_filter = st.selectbox(
        "Brand filter",
        options=["all"] + [b.value for b in Brand if b != Brand.UNASSIGNED],
        key="aeo_geo_brand",
    )
    brand = None if brand_filter == "all" else Brand(brand_filter)

    geo_reviews = list_reviews(brand=brand, kind=SeoReviewKind.GEO, limit=200)
    geo_plans = list_plans(brand=brand, kind=SeoPlanKind.GEO, limit=200)

    cols = st.columns(3)
    cols[0].metric("AEO/GEO reviews", len(geo_reviews))
    cols[1].metric("AEO/GEO plans", len(geo_plans))
    cols[2].metric("Targets (shared catalog)", count_targets(brand=brand))

    st.subheader("Reviews")
    if not geo_reviews:
        st.info(
            "No AEO/GEO reviews yet. Run `agent-crm aeo-geo-loop` or POST /aeo-geo/loop."
        )
    else:
        _render_full_csv_export(
            key="aeo_geo_reviews",
            filename=_export_filename(
                "aeo-geo-reviews", brand.value if brand else None
            ),
            fetch_all=lambda: _seo_review_rows(
                brand=brand, geo=True, limit=None, truncate=False
            ),
            preview_count=len(geo_reviews),
            preview_cap=200,
            filter_key=brand_filter,
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "id": row.id,
                        "score": row.score,
                        "status": row.status.value,
                        "brand": row.brand.value,
                        "title": row.title,
                        "one_thing": (row.one_thing or "")[:240],
                        "url": row.url,
                        "updated": row.updated_at,
                    }
                    for row in geo_reviews
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        row = _pick_seo_document(
            geo_reviews,
            label="Which AEO/GEO review to open",
            key=f"aeo_geo_review_pick_{brand_filter}",
        )
        _seo_download_buttons(
            row,
            rows=geo_reviews,
            doc="review",
            brand_filter=brand_filter,
            prefix="aeo_geo",
        )
        with st.expander(f"AEO/GEO Review — {row.domain}", expanded=True):
            st.caption(row.url)
            st.markdown(row.body)

    st.subheader("Plans (human implementation)")
    if not geo_plans:
        st.info(
            "No AEO/GEO plans yet. Owned-site audits write a plan after the review. "
            "Competitor reviews do not get plans."
        )
    else:
        _render_full_csv_export(
            key="aeo_geo_plans",
            filename=_export_filename(
                "aeo-geo-plans", brand.value if brand else None
            ),
            fetch_all=lambda: _seo_plan_rows(
                brand=brand, geo=True, limit=None, truncate=False
            ),
            preview_count=len(geo_plans),
            preview_cap=200,
            filter_key=brand_filter,
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "id": row.id,
                        "status": row.status.value,
                        "brand": row.brand.value,
                        "title": row.title,
                        "one_thing": (row.one_thing or "")[:240],
                        "tasks": len(row.tasks or []),
                        "url": row.url,
                        "review_id": row.review_id,
                        "updated": row.updated_at,
                    }
                    for row in geo_plans
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        row = _pick_seo_document(
            geo_plans,
            label="Which AEO/GEO plan to open",
            key=f"aeo_geo_plan_pick_{brand_filter}",
        )
        _seo_download_buttons(
            row,
            rows=geo_plans,
            doc="plan",
            brand_filter=brand_filter,
            prefix="aeo_geo",
        )
        with st.expander(f"AEO/GEO Plan — {row.domain}", expanded=True):
            st.caption(row.url)
            st.markdown(row.body)

