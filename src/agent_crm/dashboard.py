"""Streamlit dashboard: a readable view of the pipeline.

Run with:  streamlit run src/agent_crm/dashboard.py

The dashboard reads through the same tooling the agents use, so what you see is
exactly the store's state, not a separate query path.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pandas as pd
import streamlit as st

from agent_crm.config import get_settings
from agent_crm.db import database_kind, init_db
from agent_crm.enums import AgentStatus, Stage
from agent_crm.heartbeat import list_heartbeats
from agent_crm.pipeline import PipelineManager
from agent_crm.presence import (
    build_observer_rows,
    fetch_spark_queue_health,
    spark_slot_summary,
)
from agent_crm.tooling import CRMToolkit

_STATUS_EMOJI = {
    AgentStatus.IDLE: "⚪",
    AgentStatus.THINKING: "🟡",
    AgentStatus.WORKING: "🟢",
    AgentStatus.BLOCKED: "🔴",
}


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


def _fetch_api_agents() -> list[dict] | None:
    url = f"{get_settings().api_base_url.rstrip('/')}/agents"
    try:
        response = httpx.get(url, timeout=2.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError:
        return None


def _render_spark_strip(summary: dict) -> None:
    max_slots = int(summary.get("max_concurrency", 4))
    in_flight = list(summary.get("in_flight", []))
    external = int(summary.get("external_upstream_slots", 0))
    waiting = int(summary.get("waiting", 0))
    model = summary.get("model") or "spark"

    st.caption(
        f"Spark queue · model **{model}** · "
        f"upstream {summary.get('observed_upstream_in_flight', 0)} · "
        f"local in-flight {summary.get('local_in_flight', 0)} · "
        f"waiting {waiting}"
    )

    slot_labels: list[str] = []
    for actor in in_flight:
        slot_labels.append(f"CRM: {actor}")
    for _ in range(external):
        slot_labels.append("external / Hermes")
    while len(slot_labels) < max_slots:
        slot_labels.append("free")

    cols = st.columns(max_slots)
    for index, (col, label) in enumerate(zip(cols, slot_labels, strict=False)):
        if label == "free":
            col.success(f"Slot {index + 1}\nfree")
        elif label.startswith("external"):
            col.warning(f"Slot {index + 1}\n{label}")
        else:
            col.info(f"Slot {index + 1}\n{label}")

    waiters = summary.get("waiters", [])
    if waiters:
        st.write("Waiting for a slot:", ", ".join(waiters))


def _render_agent_observer(refresh_seconds: int) -> None:
    st.subheader("Live agent observer")
    st.caption(f"Auto-refresh every {refresh_seconds}s")

    queue_health = fetch_spark_queue_health()
    summary = spark_slot_summary(queue_health)
    _render_spark_strip(summary)

    api_agents = _fetch_api_agents()
    if api_agents is not None:
        rows = api_agents
    else:
        observer_rows = build_observer_rows(list_heartbeats(), queue_health)
        rows = [
            {
                "display_name": row.display_name,
                "status": row.status.value,
                "task": row.task,
                "resource": row.resource,
                "last_heartbeat": row.last_heartbeat,
            }
            for row in observer_rows
        ]

    if not rows:
        st.info("No agents in roster.")
        return

    table = pd.DataFrame(
        [
            {
                "agent": row.get("display_name") or row.get("name"),
                "status": f"{_STATUS_EMOJI.get(AgentStatus(row['status']), '⚪')} {row['status']}",
                "current task": row.get("task") or "—",
                "resource": row.get("resource") or "—",
                "last heartbeat": row.get("last_heartbeat") or "—",
            }
            for row in rows
        ]
    )
    st.dataframe(table, use_container_width=True, hide_index=True)


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
    df = _lead_rows()
    if df.empty:
        st.info("No leads yet. Run `agent-crm seed` or POST to /intake/webhook.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.subheader("Lead detail")
        lead_id = st.selectbox("Lead", options=df["id"].tolist())
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


def _observer_fragment(refresh_seconds: int) -> None:
    try:
        fragment = st.fragment(run_every=timedelta(seconds=refresh_seconds))
    except TypeError:
        fragment = st.fragment

    @fragment
    def _observer() -> None:
        _render_agent_observer(refresh_seconds)

    _observer()


def main() -> None:
    st.set_page_config(page_title="Agent CRM", layout="wide")
    init_db()

    settings = get_settings()
    refresh_seconds = settings.observer_refresh_seconds

    st.title("Agent CRM")
    st.caption(f"Store: {database_kind()}")

    observer_tab, pipeline_tab = st.tabs(["Live agents", "Pipeline & leads"])

    with observer_tab:
        _observer_fragment(refresh_seconds)

    with pipeline_tab:
        _render_pipeline_tab()


if __name__ == "__main__":
    main()
