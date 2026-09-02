"""Dashboard UI module: tabs.pipeline."""
from __future__ import annotations


import pandas as pd
import streamlit as st

from agent_crm.enums import AgencyRequestStatus, AgentStatus, Brand, ContactAudience, ContactVerificationStatus, HuntResourceKind, LeadSource, ResearchFindingKind, SeoPlanKind, SeoReviewKind, Stage
from agent_crm.pipeline import PipelineManager
from agent_crm.pipeline_leads import pipeline_lead_records, pipeline_leads_export_filename
from agent_crm.tooling import CRMToolkit
from agent_crm.verifier import list_verifications

from agent_crm.dashboard_ui.common import (
    _render_full_csv_export,
)

def _lead_rows(
    *,
    audience: ContactAudience | None = None,
    brand: Brand | None = None,
    limit: int | None = 500,
) -> pd.DataFrame:
    records = pipeline_lead_records(audience=audience, brand=brand, limit=limit)
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def _render_pipeline_tab() -> None:
    pm = PipelineManager(actor="dashboard")

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

    st.subheader("Leads")
    st.caption(
        "Only leads with a DNS/MX **valid** primary email appear here. "
        "Unverified, invalid, role-inbox, placeholder, filename, and disqualified rows stay in Contacts."
    )
    qual_filter = st.selectbox(
        "Qualification filter",
        options=[
            "all",
            "end_user",
            "influencer",
            "b2b",
            "client",
            "marketing",
        ],
        key="pipeline_qualification",
    )
    brand_filter = st.selectbox(
        "Brand filter",
        options=["all"] + [b.value for b in Brand if b != Brand.UNASSIGNED],
        key="pipeline_brand",
    )
    audience = None if qual_filter == "all" else ContactAudience(qual_filter)
    brand = None if brand_filter == "all" else Brand(brand_filter)
    df = _lead_rows(audience=audience, brand=brand)
    if df.empty:
        st.info("No leads yet. Run `agent-crm seed` or POST to /intake/webhook.")
    else:
        _render_full_csv_export(
            key="pipeline_leads",
            filename=pipeline_leads_export_filename(audience=audience, brand=brand),
            fetch_all=lambda: _lead_rows(
                audience=audience, brand=brand, limit=None
            ),
            preview_count=len(df),
            preview_cap=500,
            filter_key=(qual_filter, brand_filter),
        )
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.subheader("Lead detail")
        lead_id = st.selectbox("Lead", options=df["id"].tolist(), key="pipeline_lead_pick")
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

            try:
                verifications = list_verifications(int(lead_id))
                if verifications:
                    st.write("Contact verifications:")
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "contact": v.contact,
                                    "kind": v.contact_kind.value,
                                    "status": v.status.value,
                                    "reasons": "; ".join(v.reasons or []),
                                    "checked": v.checked_at,
                                    "http": v.http_status,
                                }
                                for v in verifications
                            ]
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )
            except Exception:
                pass

