"""Dashboard UI module: tabs.seo."""
from __future__ import annotations


import pandas as pd
import streamlit as st

from agent_crm.enums import AgencyRequestStatus, AgentStatus, Brand, ContactAudience, ContactVerificationStatus, HuntResourceKind, LeadSource, ResearchFindingKind, SeoPlanKind, SeoReviewKind, Stage
from agent_crm.seo_export import seo_document_markdown, seo_export_filename, zip_seo_documents
from agent_crm.seo_query_store import SeoQueryStore
from agent_crm.seo_store import count_plans, count_reviews, count_targets, list_plans, list_reviews, list_targets

from agent_crm.dashboard_ui.common import (
    _clip,
    _export_filename,
    _render_full_csv_export,
)

def _seo_target_rows(brand: Brand | None, *, limit: int | None = 200) -> pd.DataFrame:
    targets = list_targets(brand=brand, limit=limit)
    if not targets:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "role": row.role.value,
                "brand": row.brand.value,
                "domain": row.domain,
                "title": row.title,
                "url": row.url,
                "last_reviewed": row.last_reviewed_at,
                "next_review": row.next_review_at,
            }
            for row in targets
        ]
    )


def _seo_review_rows(
    *,
    brand: Brand | None,
    geo: bool,
    limit: int | None = 200,
    truncate: bool = True,
) -> pd.DataFrame:
    if geo:
        reviews = list_reviews(brand=brand, kind=SeoReviewKind.GEO, limit=limit)
    else:
        reviews = [
            row for row in list_reviews(brand=brand, limit=limit) if row.kind != SeoReviewKind.GEO
        ]
    if not reviews:
        return pd.DataFrame()
    records = []
    for row in reviews:
        record: dict[str, object] = {
            "id": row.id,
            "score": row.score,
            "status": row.status.value,
        }
        if not geo:
            record["kind"] = row.kind.value
        record.update(
            {
                "brand": row.brand.value,
                "title": row.title,
                "one_thing": _clip(row.one_thing, 240, truncate=truncate),
                "url": row.url,
                "updated": row.updated_at,
            }
        )
        records.append(record)
    return pd.DataFrame(records)


def _seo_plan_rows(
    *,
    brand: Brand | None,
    geo: bool,
    limit: int | None = 200,
    truncate: bool = True,
) -> pd.DataFrame:
    if geo:
        plans = list_plans(brand=brand, kind=SeoPlanKind.GEO, limit=limit)
    else:
        plans = [row for row in list_plans(brand=brand, limit=limit) if row.kind != SeoPlanKind.GEO]
    if not plans:
        return pd.DataFrame()
    records = []
    for row in plans:
        record: dict[str, object] = {
            "id": row.id,
            "status": row.status.value,
        }
        if not geo:
            record["kind"] = row.kind.value
        record.update(
            {
                "brand": row.brand.value,
                "title": row.title,
                "one_thing": _clip(row.one_thing, 240, truncate=truncate),
                "tasks": len(row.tasks or []),
                "url": row.url,
                "review_id": row.review_id,
                "updated": row.updated_at,
            }
        )
        records.append(record)
    return pd.DataFrame(records)


def _seo_document_label(row) -> str:
    brand = row.brand.value if getattr(row, "brand", None) is not None else ""
    domain = getattr(row, "domain", None) or ""
    title = (row.title or "").strip() or f"#{row.id}"
    return f"{brand} · {domain} · {title}"


def _seo_download_buttons(
    row, *, rows: list, doc: str, brand_filter: str, prefix: str = "seo"
) -> None:
    """Download the open file, plus a zip of every file in the current list."""
    single, bulk = st.columns([1, 1])
    if doc == "review":
        single_label = "Download review"
        bulk_label = "Export all reviews (.zip)"
    else:
        single_label = "Download plan"
        bulk_label = "Export all plans (.zip)"
    single.download_button(
        single_label,
        data=seo_document_markdown(row),
        file_name=seo_export_filename(row, doc=doc),
        mime="text/markdown",
        key=f"{prefix}_{doc}_dl_{row.id}_{brand_filter}",
        use_container_width=True,
    )
    bulk.download_button(
        bulk_label,
        data=zip_seo_documents(rows, doc=doc),
        file_name=f"{prefix}-{doc}s-{brand_filter}.zip",
        mime="application/zip",
        key=f"{prefix}_{doc}s_zip_{brand_filter}",
        use_container_width=True,
    )


def _pick_seo_document(rows: list, *, label: str, key: str):
    """Pick a review/plan by id. Keep this outside expanders — dropdowns clip inside them."""
    labels = {row.id: _seo_document_label(row) for row in rows}
    chosen_id = st.selectbox(
        label,
        options=list(labels),
        format_func=lambda pid: labels[pid],
        key=key,
    )
    return next(row for row in rows if row.id == chosen_id)


def _render_seo_tab() -> None:
    st.subheader("SEO documents")
    st.caption(
        "Reviews and implementation plans for ranch brand sites and named competitors. "
        "AEO/GEO documents live on their own tab. "
        "The agent scrapes with Firecrawl and writes markdown documents at least once a day "
        "(next pass at local noon). "
        "It never patches live pages — humans apply the plan on the target site. "
        "Download the open file as markdown, or export every file in the list as a zip."
    )

    queue = SeoQueryStore().queue_status()
    brand_filter = st.selectbox(
        "Brand filter",
        options=["all"] + [b.value for b in Brand if b != Brand.UNASSIGNED],
        key="seo_brand",
    )
    brand = None if brand_filter == "all" else Brand(brand_filter)

    qcols = st.columns(4)
    qcols[0].metric("Queued jobs (total)", queue.get("total", 0))
    qcols[1].metric("Pending", queue.get("pending", 0))
    qcols[2].metric("Reviews", count_reviews(brand=brand))
    qcols[3].metric("Plans", count_plans(brand=brand))
    st.caption(f"Targets catalogued: {count_targets(brand=brand)}")

    st.subheader("Targets")
    targets = list_targets(brand=brand, limit=200)
    if not targets:
        st.info(
            "No SEO targets yet. `agent-crm seo-loop` seeds owned sites and named competitors."
        )
    else:
        _render_full_csv_export(
            key="seo_targets",
            filename=_export_filename(
                "seo-targets", brand.value if brand else None
            ),
            fetch_all=lambda: _seo_target_rows(brand, limit=None),
            preview_count=len(targets),
            preview_cap=200,
            filter_key=brand_filter,
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "role": row.role.value,
                        "brand": row.brand.value,
                        "domain": row.domain,
                        "title": row.title,
                        "url": row.url,
                        "last_reviewed": row.last_reviewed_at,
                        "next_review": row.next_review_at,
                    }
                    for row in targets
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Reviews")
    reviews = [row for row in list_reviews(brand=brand, limit=200) if row.kind != SeoReviewKind.GEO]
    if not reviews:
        st.info("No reviews yet. Run `agent-crm seo-loop` or POST /seo/loop.")
    else:
        _render_full_csv_export(
            key="seo_reviews",
            filename=_export_filename(
                "seo-reviews", brand.value if brand else None
            ),
            fetch_all=lambda: _seo_review_rows(
                brand=brand, geo=False, limit=None, truncate=False
            ),
            preview_count=len(reviews),
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
                        "kind": row.kind.value,
                        "brand": row.brand.value,
                        "title": row.title,
                        "one_thing": (row.one_thing or "")[:240],
                        "url": row.url,
                        "updated": row.updated_at,
                    }
                    for row in reviews
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        row = _pick_seo_document(
            reviews,
            label="Which review to open",
            key=f"seo_review_pick_{brand_filter}",
        )
        _seo_download_buttons(
            row,
            rows=reviews,
            doc="review",
            brand_filter=brand_filter,
        )
        with st.expander(f"Review — {row.domain}", expanded=True):
            st.caption(row.url)
            st.markdown(row.body)

    st.subheader("Plans (human implementation)")
    plans = [row for row in list_plans(brand=brand, limit=200) if row.kind != SeoPlanKind.GEO]
    if not plans:
        st.info(
            "No plans yet. Owned-site audits write a plan after the review. "
            "Competitor reviews do not get plans."
        )
    else:
        _render_full_csv_export(
            key="seo_plans",
            filename=_export_filename(
                "seo-plans", brand.value if brand else None
            ),
            fetch_all=lambda: _seo_plan_rows(
                brand=brand, geo=False, limit=None, truncate=False
            ),
            preview_count=len(plans),
            preview_cap=200,
            filter_key=brand_filter,
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "id": row.id,
                        "status": row.status.value,
                        "kind": row.kind.value,
                        "brand": row.brand.value,
                        "title": row.title,
                        "one_thing": (row.one_thing or "")[:240],
                        "tasks": len(row.tasks or []),
                        "url": row.url,
                        "review_id": row.review_id,
                        "updated": row.updated_at,
                    }
                    for row in plans
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        row = _pick_seo_document(
            plans,
            label="Which plan to open",
            key=f"seo_plan_pick_{brand_filter}",
        )
        _seo_download_buttons(
            row,
            rows=plans,
            doc="plan",
            brand_filter=brand_filter,
        )
        with st.expander(f"Plan — {row.domain}", expanded=True):
            st.caption(row.url)
            st.markdown(row.body)

