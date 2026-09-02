"""Dashboard UI module: tabs.settings."""
from __future__ import annotations


import pandas as pd
import streamlit as st

from agent_crm.config import get_settings
from agent_crm.runtime_settings_store import docker_spark_host_hint, list_runtime_settings_meta, probe_spark_upstream, update_runtime_settings

def _render_settings_tab() -> None:
    st.subheader("Settings")
    st.caption(
        "Runtime overrides stored in the CRM database. Spark URL changes apply immediately "
        "to spark-queue; SearXNG and Firecrawl URLs apply on the next search/scrape call."
    )

    meta = list_runtime_settings_meta()
    values = {row["key"]: row["value"] for row in meta}
    defaults = {row["key"]: row["default"] for row in meta}

    with st.form("agency_settings_form"):
        spark_url = st.text_input(
            "Spark SGLang base URL",
            value=str(values.get("spark_upstream_base_url") or ""),
            help="OpenAI-compatible base URL, usually ends with /v1",
        )
        spark_model = st.text_input(
            "Spark model id",
            value=str(values.get("spark_model") or ""),
        )
        spark_cap = st.number_input(
            "Spark global session cap",
            min_value=1,
            max_value=32,
            value=int(values.get("spark_max_concurrency") or 4),
        )
        searxng = st.text_input(
            "SearXNG URL",
            value=str(values.get("searxng_url") or ""),
        )
        firecrawl = st.text_input(
            "Firecrawl URL",
            value=str(values.get("firecrawl_url") or ""),
        )
        live_refresh = st.number_input(
            "Live Agents refresh (seconds)",
            min_value=2,
            max_value=3600,
            value=int(values.get("observer_live_refresh_seconds") or 5),
        )
        token_cache = st.number_input(
            "Token totals cache (seconds)",
            min_value=10,
            max_value=86400,
            value=int(values.get("observer_refresh_seconds") or 600),
        )
        input_rate = st.number_input(
            "Est. cloud input $/M tokens",
            min_value=0.0,
            value=float(values.get("llm_input_usd_per_million") or 2.0),
            step=0.1,
        )
        output_rate = st.number_input(
            "Est. cloud output $/M tokens",
            min_value=0.0,
            value=float(values.get("llm_output_usd_per_million") or 10.0),
            step=0.1,
        )
        saved = st.form_submit_button("Save settings", type="primary")

    if saved:
        try:
            update_runtime_settings(
                {
                    "spark_upstream_base_url": spark_url,
                    "spark_model": spark_model,
                    "spark_max_concurrency": int(spark_cap),
                    "searxng_url": searxng,
                    "firecrawl_url": firecrawl,
                    "observer_live_refresh_seconds": int(live_refresh),
                    "observer_refresh_seconds": int(token_cache),
                    "llm_input_usd_per_million": float(input_rate),
                    "llm_output_usd_per_million": float(output_rate),
                }
            )
            st.success("Settings saved.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    host_hint = docker_spark_host_hint()
    if host_hint:
        st.info(
            f"Docker compose maps hostname `spark` via extra_hosts. "
            f"When Spark moves, set URL to `http://{host_hint}:8888/v1` here, "
            f"and update `spark:{host_hint}` in docker-compose.yml if you use the `spark` alias."
        )

    if st.button("Test Spark connection", key="probe_spark_settings"):
        probe = probe_spark_upstream(spark_url.strip() or None)
        if probe["ok"]:
            models = ", ".join(probe.get("models") or []) or "no models listed"
            st.success(f"Spark reachable at {probe['url']} — models: {models}")
        else:
            st.error(f"Spark probe failed: {probe.get('detail')}")

    _render_treg_settings()

    with st.expander("Environment defaults (from container env)"):
        st.json(defaults)


def _format_treg_cost(usd: float | None, cost_type: str) -> str:
    if cost_type == "free" or usd == 0:
        return "free"
    if usd is None:
        return "priced"
    return f"${usd:.4f}/{cost_type.replace('_', ' ') or 'call'}"


def _render_treg_settings() -> None:
    from agent_crm.treg.client import TregClient, TregError, treg_configured
    from agent_crm.treg.queue import allow_treg_tools, enqueue_free_treg_tools
    from agent_crm.treg.store import list_treg_tools, sync_treg_catalog, treg_counts

    st.subheader("treg paid tools")
    st.caption(
        "Free treg endpoints can run automatically. Paid people/link lookups stay "
        "off until you allow them — then they queue as hunter (people) or research (links) follow-ups."
    )
    settings = get_settings()
    counts = treg_counts()
    configured = treg_configured()
    cols = st.columns(4)
    cols[0].metric("Catalog rows", counts["total"])
    cols[1].metric("Free (auto)", counts["free"])
    cols[2].metric("Paid to pick", counts["paid_selectable"])
    cols[3].metric("Paid allowed", counts["allowed_paid"])
    if not configured:
        st.warning(
            "TREG_API_TOKEN is not set. Add the okita-2 token to `.env` and restart API/dashboard."
        )
    else:
        st.caption(f"Team `{settings.treg_org}` · {settings.treg_base_url}")
        if st.button("Check treg balance", key="treg_balance"):
            try:
                with TregClient() as client:
                    payload = client.balance()
                st.json(payload)
            except TregError as exc:
                st.error(str(exc))

    sync_cols = st.columns(2)
    with sync_cols[0]:
        do_sync = st.button("Sync treg catalog", type="primary", key="treg_sync")
    with sync_cols[1]:
        enqueue_free = st.checkbox(
            "Queue free discovery tools on sync",
            value=True,
            key="treg_enqueue_free",
        )
    if do_sync:
        try:
            result = sync_treg_catalog()
            hunt_n = 0
            research_n = 0
            if enqueue_free:
                queued = enqueue_free_treg_tools()
                hunt_n = queued.hunt_enqueued
                research_n = queued.research_enqueued
            st.success(
                f"Synced {result.upserted} endpoints "
                f"({result.free} free, {result.paid_selectable} paid jobs). "
                f"Queued {hunt_n} hunt / {research_n} research free follow-ups."
            )
            if result.errors:
                st.warning("Some catalog searches failed: " + "; ".join(result.errors[:5]))
            st.rerun()
        except TregError as exc:
            st.error(str(exc))

    free_tools = list_treg_tools(paid=False)
    if free_tools:
        with st.expander(f"Free tools already allowed ({len(free_tools)})"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "endpoint": row.endpoint_id,
                            "queue as": row.queue_as,
                            "summary": row.summary[:160],
                        }
                        for row in free_tools
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )

    paid_tools = [
        row
        for row in list_treg_tools(paid=True, selectable=True)
        if row.queue_as in {"hunter", "research"}
    ]
    if not paid_tools:
        st.info("Sync the catalog to build the paid-tool picker.")
        return

    hunter_tools = [row for row in paid_tools if row.queue_as == "hunter"]
    research_tools = [row for row in paid_tools if row.queue_as == "research"]
    hunter_labels = {
        f"{row.endpoint_id} · {_format_treg_cost(row.estimated_cost_usd, row.cost_type)} · {row.title}": row.endpoint_id
        for row in hunter_tools
        if not row.allowed
    }
    research_labels = {
        f"{row.endpoint_id} · {_format_treg_cost(row.estimated_cost_usd, row.cost_type)} · {row.title}": row.endpoint_id
        for row in research_tools
        if not row.allowed
    }
    picked = st.multiselect(
        "Hunter — find people to follow up on",
        options=list(hunter_labels),
        key="treg_hunter_pick",
    )
    picked_research = st.multiselect(
        "Research — find links to follow up on",
        options=list(research_labels),
        key="treg_research_pick",
    )
    if st.button("Allow selected paid tools and queue work", key="treg_allow"):
        ids = [hunter_labels[label] for label in picked] + [
            research_labels[label] for label in picked_research
        ]
        if not ids:
            st.error("Select at least one paid tool.")
        else:
            result = allow_treg_tools(ids)
            st.success(
                f"Allowed {len(result.allowed)}. Queued {result.hunt_enqueued} hunt "
                f"and {result.research_enqueued} research follow-ups."
            )
            st.rerun()

    allowed_paid = [row for row in paid_tools if row.allowed]
    if allowed_paid:
        st.caption("Already allowed (spend against the treg balance when hunter/research drain them):")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "endpoint": row.endpoint_id,
                        "queue as": row.queue_as,
                        "cost": _format_treg_cost(row.estimated_cost_usd, row.cost_type),
                        "summary": row.summary[:160],
                    }
                    for row in allowed_paid
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )

