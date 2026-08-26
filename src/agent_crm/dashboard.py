"""Streamlit dashboard: live agents, pipeline, and hunter resources."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pandas as pd
import streamlit as st

from agent_crm.config import get_settings
from agent_crm.contact_store import (
    count_contact_profiles,
    count_contact_profiles_by_brand,
    count_contact_profiles_by_quality,
    list_contact_profiles,
)
from agent_crm.db import database_kind, init_db
from agent_crm.enums import (
    AgentStatus,
    Brand,
    ContactAudience,
    ContactVerificationStatus,
    HuntResourceKind,
    ResearchFindingKind,
    Stage,
)
from agent_crm.hunt_feedback import parse_community_notes
from agent_crm.hunt_status import STALE_RUNNING_MINUTES, build_hunt_status
from agent_crm.heartbeat import list_heartbeats
from agent_crm.hunt_store import HuntStore
from agent_crm.pipeline import PipelineManager
from agent_crm.presence import (
    build_observer_rows,
    fetch_spark_queue_health,
    spark_slot_summary,
)
from agent_crm.research_store import list_findings
from agent_crm.tooling import CRMToolkit
from agent_crm.verifier import list_verifications

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
    rows = []
    for lead in leads:
        verification_status = "—"
        try:
            verifications = list_verifications(lead.id)
            if verifications:
                statuses = {v.status.value for v in verifications}
                if ContactVerificationStatus.INVALID.value in statuses:
                    verification_status = "invalid"
                elif ContactVerificationStatus.RISKY.value in statuses:
                    verification_status = "risky"
                elif ContactVerificationStatus.UNKNOWN.value in statuses:
                    verification_status = "unknown"
                else:
                    verification_status = "valid"
        except Exception:
            verification_status = "—"
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
                "status": lead.status.value,
                "verified": verification_status,
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


def _render_hunt_loop_status(*, compact: bool = False) -> None:
    status = build_hunt_status()
    phase = status["phase"]
    now_playing = status.get("now_playing")

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
                f"Spark waiting {status['spark']['waiting']} · "
                f"in-flight {status['spark']['in_flight']}"
            )
        return

    st.subheader("Hunt loop (live)")
    st.caption(
        f"Auto-refresh · stale running rows ignored after {STALE_RUNNING_MINUTES} minutes"
    )

    phase_cols = st.columns(4)
    phase_cols[0].metric("Phase", phase)
    phase_cols[1].metric("Pending", status.get("pending", 0))
    phase_cols[2].metric("Spark waiting", status["spark"]["waiting"])
    phase_cols[3].metric("Spark in-flight", status["spark"]["in_flight"])

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
    _render_hunt_loop_status(compact=True)

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
        _render_hunt_loop_status()

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
    st.caption("Competitor and nonprofit prospecting output from the Research agent.")

    brand_filter = st.selectbox(
        "Brand filter",
        options=["all", "celestial-nexus", "midnightsatin", "heybuddy", "tactic-studio"],
        index=0,
        key="research_brand",
    )
    kind_filter = st.selectbox(
        "Kind filter",
        options=["all", "competitor", "nonprofit", "other"],
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


def _render_contacts_tab() -> None:
    st.subheader("Contact profiles")
    st.caption("People found on scraped hunter/research pages, keyed by email.")

    CONTACTS_PAGE_SIZE = 100

    brand_filter = st.selectbox(
        "Brand filter",
        options=["all"] + [b.value for b in Brand if b != Brand.UNASSIGNED],
        key="contacts_brand",
    )
    audience_filter = st.selectbox(
        "Audience filter",
        options=["all", "marketing", "influencer", "user"],
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

    filter_key = f"{brand_filter}:{audience_filter}:{quality_filter}"
    if st.session_state.get("contacts_filter_key") != filter_key:
        st.session_state.contacts_filter_key = filter_key
        st.session_state.contacts_page = 0
    if "contacts_page" not in st.session_state:
        st.session_state.contacts_page = 0

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
        "CLI: `agent-crm verify --lead-id N` or `agent-crm verify --unverified`"
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

    observer_tab, pipeline_tab, hunter_tab, research_tab, contacts_tab, verifier_tab = st.tabs(
        ["Live agents", "Pipeline & leads", "Hunter", "Research", "Contacts", "Verifier"]
    )

    with observer_tab:
        _observer_fragment(refresh_seconds)

    with pipeline_tab:
        _render_pipeline_tab()

    with hunter_tab:
        _render_hunter_tab(refresh_seconds)

    with research_tab:
        _render_research_tab()

    with contacts_tab:
        _render_contacts_tab()

    with verifier_tab:
        _render_verifier_tab()


if __name__ == "__main__":
    main()
