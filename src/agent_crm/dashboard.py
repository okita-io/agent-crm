"""Streamlit dashboard: live agents, pipeline, and hunter resources."""

from __future__ import annotations

import hmac
import json
from datetime import UTC, datetime, timedelta

import pandas as pd
import streamlit as st

from agent_crm.comment_people_store import (
    count_comment_people,
    list_comment_people,
)
from agent_crm.config import get_settings
from agent_crm.contact_store import (
    count_contact_profiles,
    count_contact_profiles_by_brand,
    count_contact_profiles_by_quality,
    list_contact_profiles,
)
from agent_crm.db import database_kind, init_db
from agent_crm.engagement_query_store import EngagementQueryStore
from agent_crm.engagement_store import count_drafts, count_threads, list_drafts, list_threads
from agent_crm.enums import (
    AgentStatus,
    Brand,
    ContactAudience,
    ContactVerificationStatus,
    HuntResourceKind,
    ResearchFindingKind,
    Stage,
)
from agent_crm.heartbeat import list_heartbeats
from agent_crm.hunt_feedback import parse_community_notes
from agent_crm.hunt_status import STALE_RUNNING_MINUTES, build_hunt_status, infer_hunt_phase
from agent_crm.hunt_store import HuntStore
from agent_crm.improvement_store import count_open_improvement_notes, list_improvement_notes
from agent_crm.pipeline import PipelineManager
from agent_crm.pipeline_leads import list_pipeline_leads, normalize_audience
from agent_crm.presence import (
    build_observer_rows,
    fetch_spark_queue_health,
    spark_slot_summary,
)
from agent_crm.research_query_store import ResearchQueryStore
from agent_crm.research_store import list_findings
from agent_crm.seo_query_store import SeoQueryStore
from agent_crm.seo_store import (
    count_plans,
    count_reviews,
    count_targets,
    list_plans,
    list_reviews,
    list_targets,
)
from agent_crm.token_usage_store import load_token_usage_snapshot
from agent_crm.tooling import CRMToolkit
from agent_crm.verifier import list_verifications

_STATUS_EMOJI = {
    AgentStatus.IDLE: "⚪",
    AgentStatus.THINKING: "🟡",
    AgentStatus.WORKING: "🟢",
    AgentStatus.BLOCKED: "🔴",
}


def _lead_rows(
    *,
    audience: ContactAudience | None = None,
    brand: Brand | None = None,
) -> pd.DataFrame:
    leads = list_pipeline_leads(audience=audience, brand=brand, limit=500)
    if not leads:
        return pd.DataFrame()
    rows = []
    for lead in leads:
        rows.append(
            {
                "id": lead.id,
                "name": lead.name,
                "email": lead.email,
                "company": lead.company,
                "source": lead.source.value,
                "score": lead.score,
                "priority": lead.priority.value if lead.priority else None,
                "brand": lead.brand.value,
                "qualification": (
                    normalize_audience(lead.audience).value
                    if lead.audience
                    else None
                ),
                "status": lead.status.value,
                "verified": "valid",
                "created": lead.created_at,
            }
        )
    return pd.DataFrame(rows)


def _resource_rows(brand: Brand | None) -> pd.DataFrame:
    resources = HuntStore().list_resources(brand=brand, limit=500)
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


def _format_token_count(count: int) -> str:
    return f"{int(count):,}"


def _format_in_out_tokens(prompt: int, completion: int) -> str:
    if not prompt and not completion:
        return "—"
    return f"{_format_token_count(prompt)} / {_format_token_count(completion)}"


def _format_usd(amount: float) -> str:
    if amount <= 0:
        return "—"
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:,.2f}"


def _format_token_rate(rate: float) -> str:
    if rate <= 0:
        return "—"
    if rate >= 1_000_000:
        return f"{rate / 1_000_000:.1f}M / hr"
    if rate >= 10_000:
        return f"{rate / 1_000:.1f}k / hr"
    return f"{rate:,.0f} / hr"


def _token_totals_from_summary(summary: dict, rows: list[dict]) -> dict:
    usage = summary.get("token_usage") or {}
    totals = usage.get("totals") or {}
    prompt = int(totals.get("prompt_tokens") or 0)
    completion = int(totals.get("completion_tokens") or 0)
    hourly = float(totals.get("tokens_per_hour") or 0.0)
    if prompt or completion:
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "saved_usd": float(totals.get("saved_usd") or 0.0),
            "tokens_per_hour": hourly,
            "input_usd_per_million": float(usage.get("input_usd_per_million") or 2.0),
            "output_usd_per_million": float(usage.get("output_usd_per_million") or 10.0),
        }
    prompt = sum(int(row.get("prompt_tokens") or 0) for row in rows)
    completion = sum(int(row.get("completion_tokens") or 0) for row in rows)
    saved = sum(float(row.get("saved_usd") or 0.0) for row in rows)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "saved_usd": saved,
        "tokens_per_hour": 0.0,
        "input_usd_per_million": float(usage.get("input_usd_per_million") or 2.0),
        "output_usd_per_million": float(usage.get("output_usd_per_million") or 10.0),
    }


def _format_duration(seconds: int) -> str:
    minutes, remainder = divmod(max(0, seconds), 60)
    if minutes:
        return f"{minutes}m {remainder}s"
    return f"{remainder}s"


def _priority_label(priority: int) -> str:
    labels = {
        100: "tactic marketing",
        90: "tactic influencer",
        80: "tactic user",
        70: "midnightsatin influencer",
        65: "midnightsatin user",
    }
    label = labels.get(priority)
    if label:
        return f"{label} ({priority})"
    if priority == 30:
        return f"default ({priority})"
    return str(priority)


def _observer_live_refresh_seconds() -> int:
    """Spark slots + heartbeat/status/task. Floor at 2s to avoid a busy loop."""
    return max(2, int(get_settings().observer_live_refresh_seconds or 5))


def _observer_refresh_seconds() -> int:
    """Token totals / hunt snapshot. Floor at 10s so 0 cannot hammer Postgres."""
    return max(10, int(get_settings().observer_refresh_seconds or 600))


def _format_refresh_interval(seconds: int) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        minutes = seconds // 60
        return "1 min" if minutes == 1 else f"{minutes} min"
    if seconds >= 60:
        minutes, remainder = divmod(seconds, 60)
        return f"{minutes}m {remainder}s"
    return f"{seconds}s"


_CACHE_TTL = _observer_refresh_seconds()

# Streamlit greys out the previous frame (opacity 0.33 after 0.5s) while a
# rerun or fragment refresh is in flight. Live-agent polling hits this often.
_STALE_FADE_CSS = """
.stApp [data-testid="stElementContainer"],
.stApp [data-testid="stVerticalBlock"],
.stApp [data-testid="stHorizontalBlock"],
.stApp [data-testid="stVerticalBlockBorderWrapper"],
.stApp [data-stale="true"],
.stApp .stElementContainer,
.stApp .stVerticalBlock,
.stApp .stHorizontalBlock,
.stApp .element-container {
    opacity: 1 !important;
    transition: none !important;
}
"""


def _disable_stale_fade() -> None:
    html = f"<style>{_STALE_FADE_CSS}</style>"
    inject = getattr(st, "html", None)
    if callable(inject):
        inject(html)
        return
    st.markdown(html, unsafe_allow_html=True)


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def _cached_hunt_status() -> dict:
    return build_hunt_status()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def _cached_token_snapshot() -> dict:
    return load_token_usage_snapshot()


def _clear_live_caches() -> None:
    _cached_hunt_status.clear()
    _cached_token_snapshot.clear()


def _render_live_refresh_bar(
    refresh_seconds: int,
    *,
    key: str,
    token_seconds: int | None = None,
) -> None:
    left, right = st.columns([4, 1])
    with left:
        stamp = datetime.now(UTC).strftime("%H:%M:%S")
        if token_seconds is None:
            caption = (
                f"Cached snapshot · auto-refresh every "
                f"{_format_refresh_interval(refresh_seconds)} · updated {stamp} UTC"
            )
        else:
            caption = (
                f"Slots / status every {_format_refresh_interval(refresh_seconds)} · "
                f"token totals every {_format_refresh_interval(token_seconds)} · "
                f"updated {stamp} UTC"
            )
        st.caption(caption)
    with right:
        if st.button("Refresh now", key=key, use_container_width=True):
            _clear_live_caches()


def _render_hunt_loop_status(
    *,
    compact: bool = False,
    refresh_seconds: int | None = None,
    live_spark: dict | None = None,
) -> None:
    status = _cached_hunt_status()
    now_playing = status.get("now_playing")
    spark = live_spark if live_spark is not None else status.get("spark") or {}
    in_flight_count = (
        len(spark["in_flight"])
        if isinstance(spark.get("in_flight"), list)
        else int(spark.get("in_flight") or 0)
    )
    if live_spark is not None:
        phase = infer_hunt_phase(
            has_fresh_running=now_playing is not None,
            spark_waiting=int(spark.get("waiting") or 0),
            spark_in_flight=in_flight_count,
        )
    else:
        phase = status["phase"]

    if compact:
        if now_playing:
            st.caption(
                f"Hunt loop · **{phase}** · "
                f"{now_playing['brand']} p{now_playing['priority']} · "
                f"\"{(now_playing['query'] or '')[:72]}\" · "
                f"running {_format_duration(now_playing['running_seconds'])}"
            )
        else:
            pending = status.get("pending", 0)
            st.caption(
                f"Hunt loop · **{phase}** · pending {pending} · "
                f"Spark waiting {spark.get('waiting', 0)} · "
                f"in-flight {in_flight_count}"
            )
        return

    st.subheader("Hunt loop (live)")
    if refresh_seconds is not None:
        _render_live_refresh_bar(refresh_seconds, key="hunter_refresh_now")
    st.caption(
        f"Stale running rows ignored after {STALE_RUNNING_MINUTES} minutes"
    )

    phase_cols = st.columns(4)
    phase_cols[0].metric("Phase", phase)
    phase_cols[1].metric("Pending", status.get("pending", 0))
    phase_cols[2].metric("Spark waiting", status["spark"]["waiting"])
    phase_cols[3].metric("Spark in-flight", status["spark"]["in_flight"])

    enrich_pending = int((status.get("agent_jobs") or {}).get("pending_by_kind", {}).get("enrich_contact", 0))
    verify_pending = int((status.get("agent_jobs") or {}).get("pending_by_kind", {}).get("verify_lead", 0))
    st.caption(
        f"Job queue: enrich **{enrich_pending}** pending · "
        f"verify **{verify_pending}** pending"
    )

    st.markdown("**Now playing**")
    if now_playing:
        audience = now_playing.get("audience") or "—"
        st.write(
            f"**{now_playing['brand']}** · priority {now_playing['priority']} · "
            f"origin `{now_playing['origin']}` · audience **{audience}** · "
            f"running **{_format_duration(now_playing['running_seconds'])}** "
            f"(updated {now_playing['updated_at']})"
        )
        st.code(now_playing["query"], language=None)
    else:
        st.info("No fresh running query — loop is idle or between queries.")

    st.markdown("**Queue breakdown**")
    breakdown = status.get("queue_breakdown") or []
    if breakdown:
        queue_df = pd.DataFrame(
            [
                {
                    "brand": row["brand"],
                    "priority": _priority_label(row["priority"]),
                    "status": row["status"],
                    "count": row["count"],
                }
                for row in breakdown
            ]
        )
        st.dataframe(queue_df, use_container_width=True, hide_index=True)
    else:
        st.info("Queue is empty.")

    st.markdown("**Pete's list** (person emails on tactic.studio profiles)")
    tactic_total = int(status.get("tactic_studio_email_total", 0))
    tactic_all = int(status.get("tactic_studio_all_email_total", tactic_total))
    tactic_goal = int(status.get("tactic_studio_email_goal", 100))
    st.progress(min(tactic_total / tactic_goal, 1.0))
    st.caption(
        f"tactic.studio person emails: **{tactic_total}** / {tactic_goal} goal "
        f"(all with email: {tactic_all})"
    )

    email_counts = status.get("email_counts") or []
    if email_counts:
        st.dataframe(
            pd.DataFrame(email_counts),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No contact profiles with email yet.")

    st.markdown("**Recently completed**")
    completed = status.get("recently_completed") or []
    if completed:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "query": row["query"],
                        "brand": row["brand"],
                        "updated": row["updated_at"],
                    }
                    for row in completed
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No completed hunt queries yet.")


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


def _render_agent_observer(*, live_seconds: int, token_seconds: int) -> None:
    st.subheader("Live agent observer")
    _render_live_refresh_bar(
        live_seconds,
        key="observer_refresh_now",
        token_seconds=token_seconds,
    )

    queue_health = fetch_spark_queue_health()
    token_snapshot = _cached_token_snapshot()
    summary = spark_slot_summary(queue_health, persisted_usage=token_snapshot)
    _render_spark_strip(summary)
    _render_hunt_loop_status(compact=True, live_spark=summary)

    observer_rows = build_observer_rows(
        list_heartbeats(),
        queue_health,
        persisted_usage=token_snapshot,
    )
    rows = [
        {
            "display_name": row.display_name,
            "status": row.status.value,
            "task": row.task,
            "resource": row.resource,
            "last_heartbeat": row.last_heartbeat,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "saved_usd": row.saved_usd,
            "tokens_per_hour": row.tokens_per_hour,
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
                "in / out tokens": _format_in_out_tokens(
                    int(row.get("prompt_tokens") or 0),
                    int(row.get("completion_tokens") or 0),
                ),
                "tok / hr": _format_token_rate(float(row.get("tokens_per_hour") or 0.0)),
                "est. savings": _format_usd(float(row.get("saved_usd") or 0.0)),
                "last heartbeat": row.get("last_heartbeat") or "—",
            }
            for row in rows
        ]
    )
    st.dataframe(table, use_container_width=True, hide_index=True)

    totals = _token_totals_from_summary(summary, rows)
    prompt = int(totals["prompt_tokens"])
    completion = int(totals["completion_tokens"])
    saved = float(totals["saved_usd"])
    hourly = float(totals.get("tokens_per_hour") or 0.0)
    in_rate = float(totals["input_usd_per_million"])
    out_rate = float(totals["output_usd_per_million"])
    total_tokens = prompt + completion

    st.subheader("Local GPU vs cloud APIs")
    metric_cols = st.columns(5)
    metric_cols[0].metric("Tokens in", _format_token_count(prompt))
    metric_cols[1].metric("Tokens out", _format_token_count(completion))
    metric_cols[2].metric("Total tokens", _format_token_count(total_tokens))
    metric_cols[3].metric("Avg tok / hr", _format_token_rate(hourly) if hourly else "0 / hr")
    metric_cols[4].metric("Est. cloud cost avoided", _format_usd(saved) if saved else "$0.00")
    st.caption(
        "Lifetime totals persist in the CRM database. Hourly rate is total tokens "
        "divided by hours since that agent's first recorded completion. "
        "Counted from Spark ``usage`` when present, otherwise ~4 chars/token. "
        f"Avoided spend at **${in_rate:.2f}** / million input "
        f"and **${out_rate:.2f}** / million output — typical cloud API rates. "
        "The desk GPU is unmetered."
    )


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
    community_kinds = (
        HuntResourceKind.COMMUNITY,
        HuntResourceKind.FORUM,
        HuntResourceKind.SOCIAL,
    )
    communities = store.list_resources(brand=brand, kinds=community_kinds, limit=200)
    if not communities:
        st.info("No community resources catalogued yet.")
    else:
        st.dataframe(
            pd.DataFrame(
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
                    for row in communities
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Derived hunt queries")
    st.caption(
        "Deterministic follow-ups from discovered communities and extracted contact names. "
        "Inspect `origin` on `hunt_queries` (prefix `community:` or `person:`); "
        "`GET /hunt/queue` reports aggregate pending counts."
    )
    derived = store.list_feedback_queries(brand=brand, limit=200)
    if not derived:
        st.info("No community/person feedback queries yet.")
    else:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "origin": row.origin,
                        "query": row.query,
                        "status": row.status.value,
                        "brand": row.brand.value,
                        "created": row.created_at,
                    }
                    for row in derived
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("All hunter resources")
    df = _resource_rows(brand)
    if df.empty:
        st.info("No hunter resources yet. Run `agent-crm hunt-loop --brand midnightsatin`.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)


def _render_research_tab() -> None:
    st.subheader("Research findings")
    st.caption(
        "Competitor, nonprofit, and ad-placement prospecting. The query queue is "
        "append-only: SearXNG/Firecrawl pages enqueue new search terms and rows are never deleted."
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
        options=["all", "competitor", "nonprofit", "ad_placement", "other"],
        index=0,
    )

    brand = Brand(brand_filter) if brand_filter != "all" else None
    kind = ResearchFindingKind(kind_filter) if kind_filter != "all" else None
    findings = list_findings(brand=brand, kind=kind, limit=500)

    if not findings:
        st.info(
            "No findings yet. Run `agent-crm research --brand celestial-nexus` "
            "or POST to /research."
        )
        return

    st.metric("Findings", len(findings))
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "id": row.id,
                    "brand": row.brand.value,
                    "kind": row.kind.value,
                    "domain": row.domain,
                    "title": row.title,
                    "url": row.url,
                    "summary": row.summary[:240] + ("…" if len(row.summary) > 240 else ""),
                    "source query": row.source_query,
                    "extra": json.dumps(row.extra) if row.extra else None,
                    "last seen": row.last_seen_at,
                }
                for row in findings
            ]
        ),
        use_container_width=True,
        hide_index=True,
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
    threads = list_threads(brand=brand, limit=200)
    if not threads:
        st.info(
            "No threads yet. The hunter catalogs forums, then `agent-crm engagement-loop` "
            "scans them for popular posts."
        )
    else:
        st.dataframe(
            pd.DataFrame(
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
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Draft replies (not posted)")
    drafts = list_drafts(brand=brand, limit=200)
    if not drafts:
        st.info("No drafts yet. Engagement loop writes drafts when a thread is popular enough.")
    else:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "status": row.status.value,
                        "brand": row.brand.value,
                        "angle": row.product_angle,
                        "draft": row.draft_text[:400],
                        "thread_id": row.thread_id,
                        "updated": row.updated_at,
                    }
                    for row in drafts
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )


def _seo_document_label(row) -> str:
    brand = row.brand.value if getattr(row, "brand", None) is not None else ""
    domain = getattr(row, "domain", None) or ""
    title = (row.title or "").strip() or f"#{row.id}"
    return f"{brand} · {domain} · {title}"


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
        "The agent scrapes with Firecrawl and writes markdown documents at least once a day "
        "(next pass at local noon). "
        "It never patches live pages — humans apply the plan on the target site."
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
    reviews = list_reviews(brand=brand, limit=200)
    if not reviews:
        st.info("No reviews yet. Run `agent-crm seo-loop` or POST /seo/loop.")
    else:
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
        with st.expander(f"Review — {row.domain}", expanded=True):
            st.caption(row.url)
            st.markdown(row.body)

    st.subheader("Plans (human implementation)")
    plans = list_plans(brand=brand, limit=200)
    if not plans:
        st.info(
            "No plans yet. Owned-site audits write a plan after the review. "
            "Competitor reviews do not get plans."
        )
    else:
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
        with st.expander(f"Plan — {row.domain}", expanded=True):
            st.caption(row.url)
            st.markdown(row.body)


def _render_contacts_tab() -> None:
    st.subheader("Contact profiles")
    st.caption(
        "People found on scraped hunter/research pages. Email contacts are keyed by address; "
        "comment authors are keyed by platform + handle (no email invented). "
        "Title, organization, and location on email rows come from public-web enrichment."
    )

    CONTACTS_PAGE_SIZE = 100

    view_filter = st.selectbox(
        "View",
        options=["emails", "commenters", "all"],
        index=0,
        key="contacts_view",
    )

    brand_filter = st.selectbox(
        "Brand filter",
        options=["all"] + [b.value for b in Brand if b != Brand.UNASSIGNED],
        key="contacts_brand",
    )
    audience_filter = st.selectbox(
        "Audience filter",
        options=[
            "all",
            "marketing",
            "influencer",
            "end_user",
            "b2b",
            "client",
            "user",
        ],
        key="contacts_audience",
    )
    quality_filter = st.selectbox(
        "Email quality",
        options=["person", "role", "all"],
        index=0,
        key="contacts_quality",
    )
    brand = None if brand_filter == "all" else Brand(brand_filter)
    audience = (
        None
        if audience_filter == "all"
        else ContactAudience(audience_filter)
    )
    quality = quality_filter

    filter_key = f"{view_filter}:{brand_filter}:{audience_filter}:{quality_filter}"
    if st.session_state.get("contacts_filter_key") != filter_key:
        st.session_state.contacts_filter_key = filter_key
        st.session_state.contacts_page = 0
    if "contacts_page" not in st.session_state:
        st.session_state.contacts_page = 0

    if view_filter in {"commenters", "all"}:
        comment_total = count_comment_people(brand=brand, audience=audience)
        st.caption(f"Comment authors (handles): **{comment_total}**")

    if view_filter == "commenters":
        _render_comment_people_table(
            brand=brand,
            audience=audience,
            page_size=CONTACTS_PAGE_SIZE,
        )
        return

    quality_counts = count_contact_profiles_by_quality(brand=brand, audience=audience)
    total = count_contact_profiles(brand=brand, audience=audience, quality=quality)
    st.caption(
        f"person **{quality_counts['person']}** / "
        f"role **{quality_counts['role']}** / "
        f"total **{quality_counts['total']}**"
    )

    if quality_counts["total"] == 0:
        st.info("No contact profiles yet. Run hunter or research scrapes to extract emails.")
        return

    if brand is None and quality == "all":
        by_brand = count_contact_profiles_by_brand(audience=audience)
        brand_cols = st.columns(max(len(by_brand), 1))
        for col, row in zip(brand_cols, by_brand, strict=False):
            col.metric(row["brand"], row["count"])
    else:
        st.metric("Profiles", total)

    page = st.session_state.contacts_page
    max_page = max((total - 1) // CONTACTS_PAGE_SIZE, 0)
    page = min(page, max_page)
    st.session_state.contacts_page = page
    offset = page * CONTACTS_PAGE_SIZE

    profiles = list_contact_profiles(
        brand=brand,
        audience=audience,
        quality=quality,
        limit=CONTACTS_PAGE_SIZE,
        offset=offset,
    )

    if profiles:
        showing_start = offset + 1
        showing_end = offset + len(profiles)
        st.caption(f"Showing {showing_start}–{showing_end} of {total} matching")
    else:
        st.caption(f"Showing 0 of {total} matching")

    nav_prev, nav_next, _ = st.columns([1, 1, 6])
    if nav_prev.button("Previous", disabled=page <= 0, key="contacts_prev"):
        st.session_state.contacts_page = max(page - 1, 0)
        st.rerun()
    if nav_next.button("Next", disabled=page >= max_page, key="contacts_next"):
        st.session_state.contacts_page = min(page + 1, max_page)
        st.rerun()

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "name": row.name,
                    "title": row.title,
                    "organization": row.organization,
                    "location": row.location,
                    "email": row.email,
                    "brand": row.brand.value,
                    "audience": row.audience.value if row.audience else None,
                    "socials": json.dumps(row.socials) if row.socials else None,
                    "source pages": ", ".join(row.source_urls or []),
                    "lead_id": row.lead_id,
                    "updated": row.updated_at,
                }
                for row in profiles
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    if view_filter == "all":
        st.divider()
        st.subheader("Comment authors")
        _render_comment_people_table(
            brand=brand,
            audience=audience,
            page_size=CONTACTS_PAGE_SIZE,
            key_prefix="all_",
        )


def _render_comment_people_table(
    *,
    brand: Brand | None,
    audience: ContactAudience | None,
    page_size: int,
    key_prefix: str = "",
) -> None:
    total = count_comment_people(brand=brand, audience=audience)
    if total == 0:
        st.info("No comment authors yet. Run hunter or research scrapes on threads/articles.")
        return

    page_key = f"{key_prefix}comment_people_page"
    if page_key not in st.session_state:
        st.session_state[page_key] = 0
    page = st.session_state[page_key]
    max_page = max((total - 1) // page_size, 0)
    page = min(page, max_page)
    st.session_state[page_key] = page
    offset = page * page_size

    people = list_comment_people(
        brand=brand,
        audience=audience,
        limit=page_size,
        offset=offset,
    )
    if people:
        showing_start = offset + 1
        showing_end = offset + len(people)
        st.caption(f"Showing {showing_start}–{showing_end} of {total} comment authors")
    else:
        st.caption(f"Showing 0 of {total} comment authors")

    nav_prev, nav_next, _ = st.columns([1, 1, 6])
    if nav_prev.button(
        "Previous",
        disabled=page <= 0,
        key=f"{key_prefix}comment_people_prev",
    ):
        st.session_state[page_key] = max(page - 1, 0)
        st.rerun()
    if nav_next.button(
        "Next",
        disabled=page >= max_page,
        key=f"{key_prefix}comment_people_next",
    ):
        st.session_state[page_key] = min(page + 1, max_page)
        st.rerun()

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "platform": row.platform,
                    "handle": row.handle,
                    "display name": row.display_name,
                    "profile": row.profile_url,
                    "brand": row.brand.value,
                    "audience": row.audience.value if row.audience else None,
                    "source pages": ", ".join(row.source_urls or []),
                    "snippet": (
                        (row.comment_snippets or [{}])[-1].get("snippet")
                        if row.comment_snippets
                        else None
                    ),
                    "updated": row.updated_at,
                }
                for row in people
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )


def _render_verifier_tab() -> None:
    st.subheader("Lead verifier")
    st.caption("Defensive DNS/MX/HTTP checks — no mail is ever sent.")

    crm = CRMToolkit(actor="dashboard")
    leads = crm.list_leads(limit=500)
    hunter_leads = [lead for lead in leads if lead.source.value == "hunter"]

    st.metric("Hunter leads", len(hunter_leads))

    rows = []
    for lead in hunter_leads:
        try:
            verifications = list_verifications(lead.id)
        except Exception:
            verifications = []
        if not verifications:
            rows.append(
                {
                    "lead_id": lead.id,
                    "name": lead.name,
                    "company": lead.company,
                    "email": lead.email,
                    "verification": "unverified",
                    "contacts": 0,
                }
            )
            continue
        worst = ContactVerificationStatus.VALID
        rank = {
            ContactVerificationStatus.VALID: 0,
            ContactVerificationStatus.UNKNOWN: 1,
            ContactVerificationStatus.RISKY: 2,
            ContactVerificationStatus.INVALID: 3,
        }
        for v in verifications:
            if rank[v.status] > rank[worst]:
                worst = v.status
        rows.append(
            {
                "lead_id": lead.id,
                "name": lead.name,
                "company": lead.company,
                "email": lead.email,
                "verification": worst.value,
                "contacts": len(verifications),
            }
        )

    if not rows:
        st.info("No hunter leads yet. Run `agent-crm hunt` first.")
    else:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.caption(
        "CLI: `agent-crm verify --lead-id N` or `agent-crm verify --unverified`. "
        "Contact title/org enrichment runs separately via public-web search on the Contacts tab."
    )


def _render_improvement_tab() -> None:
    st.subheader("Improvement / gaps")
    st.caption(
        "Self-learning notes from the orchestrator and workers. "
        "Manager/Cursor can pull open items via GET /improvement-notes?status=open."
    )

    open_count = count_open_improvement_notes()
    st.metric("Open notes", open_count)

    notes = list_improvement_notes(limit=200)
    if not notes:
        st.info("No improvement notes yet. The orchestrator records gaps as it inspects the stack.")
        return

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "id": note.id,
                    "severity": note.severity.value,
                    "kind": note.kind.value,
                    "source": note.source_agent.value,
                    "title": note.title,
                    "status": note.status.value,
                    "suggested_fix": note.suggested_fix,
                    "created": note.created_at,
                }
                for note in notes
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    for note in notes[:10]:
        with st.expander(f"[{note.severity.value}] {note.title}"):
            st.write(note.body)
            if note.metrics:
                st.json(note.metrics)
            if note.suggested_fix:
                st.caption(f"Suggested fix: {note.suggested_fix}")


def _observer_fragment(*, live_seconds: int, token_seconds: int) -> None:
    try:
        fragment = st.fragment(run_every=timedelta(seconds=live_seconds))
    except TypeError:
        fragment = st.fragment

    @fragment
    def _observer() -> None:
        _render_agent_observer(live_seconds=live_seconds, token_seconds=token_seconds)

    _observer()


def _require_dashboard_access() -> bool:
    """Optional Streamlit password gate. Empty ``CRM_DASHBOARD_PASSWORD`` skips it."""
    expected = get_settings().dashboard_password.strip()
    if not expected:
        return True
    if st.session_state.get("dashboard_unlocked") is True:
        return True
    st.title("Agent CRM+SEO")
    st.caption("This dashboard is password-protected.")
    entered = st.text_input("Password", type="password", key="dashboard_password_input")
    if st.button("Unlock", type="primary"):
        if hmac.compare_digest(entered, expected):
            st.session_state.dashboard_unlocked = True
            st.rerun()
        st.error("Invalid password")
    return False


def main() -> None:
    st.set_page_config(page_title="Agent CRM+SEO", layout="wide")
    _disable_stale_fade()
    init_db()
    if not _require_dashboard_access():
        return

    live_seconds = _observer_live_refresh_seconds()
    refresh_seconds = _observer_refresh_seconds()

    st.title("Agent CRM+SEO")
    st.caption(f"Store: {database_kind()}")

    observer_tab, pipeline_tab, hunter_tab, research_tab, engagement_tab, seo_tab, contacts_tab, verifier_tab, improvement_tab = st.tabs(
        [
            "Live agents",
            "Pipeline & leads",
            "Hunter",
            "Research",
            "Engagement",
            "SEO",
            "Contacts",
            "Verifier",
            "Improvement",
        ]
    )

    with observer_tab:
        _observer_fragment(live_seconds=live_seconds, token_seconds=refresh_seconds)

    with pipeline_tab:
        _render_pipeline_tab()

    with hunter_tab:
        _render_hunter_tab(refresh_seconds)

    with research_tab:
        _render_research_tab()

    with engagement_tab:
        _render_engagement_tab()

    with seo_tab:
        _render_seo_tab()

    with contacts_tab:
        _render_contacts_tab()

    with verifier_tab:
        _render_verifier_tab()

    with improvement_tab:
        _render_improvement_tab()


if __name__ == "__main__":
    main()
