"""Streamlit dashboard: pipeline view + hunter resources."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from agent_crm.db import database_kind, init_db
from agent_crm.enums import Brand, Stage
from agent_crm.hunt_store import HuntStore
from agent_crm.pipeline import PipelineManager
from agent_crm.tooling import CRMToolkit


def _lead_rows() -> pd.DataFrame:
    crm = CRMToolkit(actor="dashboard")
    leads = crm.list_leads(limit=500)
    if not leads:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "id": lead.id,
                "name": lead.name,
                "email": lead.email,
                "company": lead.company,
                "source": lead.source.value,
                "score": lead.score,
                "priority": lead.priority.value if lead.priority else None,
                "brand": lead.brand.value,
                "status": lead.status.value,
                "created": lead.created_at,
            }
            for lead in leads
        ]
    )


def _resource_rows(brand: Brand | None) -> pd.DataFrame:
    store = HuntStore()
    resources = store.list_resources(brand=brand, limit=500)
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
                "found_via": (r.found_via_query or "")[:80],
                "url": r.url,
                "last_seen": r.last_seen,
            }
            for r in resources
        ]
    )


def _render_leads_tab() -> None:
    st.subheader("Leads")
    df = _lead_rows()
    if df.empty:
        st.info("No leads yet. Run `agent-crm seed` or POST to /intake/webhook.")
        return

    st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("Lead detail")
    lead_id = st.selectbox("Lead", options=df["id"].tolist(), key="lead_select")
    if lead_id:
        crm = CRMToolkit(actor="dashboard")
        activities = crm.list_activities(int(lead_id))
        st.write("Activity history (append-only):")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "when": a.created_at,
                        "actor": a.actor,
                        "type": a.type.value,
                        "summary": a.summary,
                    }
                    for a in activities
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )


def _render_hunter_tab() -> None:
    st.subheader("Hunter resources")
    store = HuntStore()
    status = store.queue_status()

    cols = st.columns(3)
    cols[0].metric("Pending queries", status["pending"])
    cols[1].metric("Total resources", status["total_resources"])
    cols[2].metric("Completed queries", status["by_status"].get("completed", 0))

    brand_filter = st.selectbox(
        "Brand filter",
        options=["all"] + [b.value for b in Brand if b != Brand.UNASSIGNED],
        key="resource_brand",
    )
    brand = None if brand_filter == "all" else Brand(brand_filter)
    df = _resource_rows(brand)
    if df.empty:
        st.info("No hunter resources yet. Run `agent-crm hunt-loop --brand midnightsatin`.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="Agent CRM", layout="wide")
    init_db()

    pm = PipelineManager(actor="dashboard")

    st.title("Agent CRM")
    st.caption(f"Store: {database_kind()}")

    report = pm.weekly_report()
    cols = st.columns(5)
    cols[0].metric("New leads (7d)", report["new_leads"])
    cols[1].metric("Open opportunities", report["open_opportunities"])
    cols[2].metric("Hot leads", report["hot_leads"])
    cols[3].metric("Won (7d)", report["won"])
    cols[4].metric("Lost (7d)", report["lost"])

    st.subheader("Pipeline by stage")
    stage_counts = report["stage_counts"]
    stage_df = pd.DataFrame(
        {"stage": [s.value for s in Stage], "count": [stage_counts[s.value] for s in Stage]}
    ).set_index("stage")
    st.bar_chart(stage_df)

    leads_tab, hunter_tab = st.tabs(["Leads", "Hunter"])
    with leads_tab:
        _render_leads_tab()
    with hunter_tab:
        _render_hunter_tab()


if __name__ == "__main__":
    main()
