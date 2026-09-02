"""Streamlit dashboard: live agents, pipeline, and hunter resources."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pandas as pd
import streamlit as st

from agent_crm.agent_control import set_agent_enabled
from agent_crm.agency_request_store import (
    create_agency_request,
    list_agency_requests,
)
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
    AgencyRequestStatus,
    AgentStatus,
    Brand,
    ContactAudience,
    ContactVerificationStatus,
    HuntResourceKind,
    LeadSource,
    ResearchFindingKind,
    SeoPlanKind,
    SeoReviewKind,
    Stage,
)
from agent_crm.heartbeat import list_heartbeats
from agent_crm.hunt_feedback import parse_community_notes
from agent_crm.hunt_status import STALE_RUNNING_MINUTES, build_hunt_status, infer_hunt_phase
from agent_crm.hunt_store import HuntStore
from agent_crm.improvement_store import count_open_improvement_notes, list_improvement_notes
from agent_crm.pipeline import PipelineManager
from agent_crm.pipeline_leads import (
    pipeline_lead_records,
    pipeline_leads_export_filename,
)
from agent_crm.presence import (
    build_observer_rows,
    fetch_spark_queue_health,
    spark_slot_summary,
)
from agent_crm.research_query_store import ResearchQueryStore
from agent_crm.research_store import list_findings
from agent_crm.seo_export import seo_document_markdown, seo_export_filename, zip_seo_documents
from agent_crm.seo_query_store import SeoQueryStore
from agent_crm.seo_store import (
    count_plans,
    count_reviews,
    count_targets,
    list_plans,
    list_reviews,
    list_targets,
)
from agent_crm.runtime_settings_store import (
    docker_spark_host_hint,
    list_runtime_settings_meta,
    probe_spark_upstream,
    update_runtime_settings,
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

_ROSTER_COL_WEIGHTS = [0.55, 1.7, 1.35, 2.2, 1.7, 1.4, 1.0, 1.1, 1.6]
_ROSTER_HEADERS = (
    "on",
    "agent",
    "status",
    "current task",
    "resource",
    "in / out tokens",
    "tok / hr",
    "est. savings",
    "last heartbeat",
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


def _clip(value: str | None, length: int, *, truncate: bool) -> str | None:
    if not value:
        return value
    if truncate and len(value) > length:
        return value[:length] + "…"
    return value


def _export_filename(stem: str, *parts: str | None) -> str:
    bits = [stem]
    for part in parts:
        if part:
            bits.append(part)
    return "-".join(bits) + ".csv"


def _dataframe_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def _render_full_csv_export(
    *,
    key: str,
    filename: str,
    fetch_all: Callable[[], pd.DataFrame],
    preview_count: int,
    preview_cap: int | None = None,
    filter_key: object = (),
    caption: str | None = None,
) -> None:
    """On-demand CSV of every matching row; preview tables stay capped."""
    actions, note = st.columns([1, 3])
    with actions:
        prepare = st.button(
            "Full export",
            key=f"{key}_prepare",
            use_container_width=True,
            help="CSV of every matching row, not just the preview in the table.",
        )
    with note:
        if caption is not None:
            st.caption(caption)
        elif preview_cap is not None:
            st.caption(
                f"Table shows up to {preview_cap} matches ({preview_count} loaded). "
                "Full export includes every matching row."
            )
        else:
            st.caption(
                f"Table shows {preview_count} rows. "
                "Full export includes every matching row."
            )
    cache_name = f"{key}_csv"
    if prepare:
        with st.spinner("Building full CSV…"):
            full_df = fetch_all()
            csv_bytes = _dataframe_csv_bytes(full_df)
        st.session_state[cache_name] = {
            "filters": filter_key,
            "csv": csv_bytes,
            "name": filename,
            "rows": int(len(full_df)),
        }
    cached = st.session_state.get(cache_name)
    if isinstance(cached, dict) and cached.get("filters") == filter_key:
        st.download_button(
            f"Download CSV ({cached['rows']} rows)",
            data=cached["csv"],
            file_name=cached["name"],
            mime="text/csv",
            key=f"{key}_download",
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


def _research_finding_rows(
    *,
    brand: Brand | None,
    kind: ResearchFindingKind | None,
    limit: int | None = 500,
    truncate: bool = True,
) -> pd.DataFrame:
    findings = list_findings(brand=brand, kind=kind, limit=limit)
    if not findings:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "id": row.id,
                "brand": row.brand.value,
                "kind": row.kind.value,
                "domain": row.domain,
                "title": row.title,
                "url": row.url,
                "summary": _clip(row.summary, 240, truncate=truncate),
                "source query": row.source_query,
                "extra": json.dumps(row.extra) if row.extra else None,
                "last seen": row.last_seen_at,
            }
            for row in findings
        ]
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


def _contact_profile_rows(
    *,
    brand: Brand | None,
    audience: ContactAudience | None,
    quality: str,
    limit: int | None = 100,
    offset: int = 0,
) -> pd.DataFrame:
    profiles = list_contact_profiles(
        brand=brand,
        audience=audience,
        quality=quality,
        limit=limit,
        offset=offset,
    )
    if not profiles:
        return pd.DataFrame()
    return pd.DataFrame(
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
    )


def _comment_people_rows(
    *,
    brand: Brand | None,
    audience: ContactAudience | None,
    limit: int | None = 100,
    offset: int = 0,
) -> pd.DataFrame:
    people = list_comment_people(
        brand=brand,
        audience=audience,
        limit=limit,
        offset=offset,
    )
    if not people:
        return pd.DataFrame()
    return pd.DataFrame(
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
    )


def _verifier_rows(*, limit: int | None = 500) -> pd.DataFrame:
    crm = CRMToolkit(actor="dashboard")
    hunter_leads = crm.list_leads(source=LeadSource.HUNTER, limit=limit)
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
        for verification in verifications:
            if rank[verification.status] > rank[worst]:
                worst = verification.status
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
    return pd.DataFrame(rows)


def _improvement_note_rows(*, limit: int | None = 200) -> pd.DataFrame:
    notes = list_improvement_notes(limit=limit)
    if not notes:
        return pd.DataFrame()
    return pd.DataFrame(
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
    from agent_crm.runtime_settings_store import get_runtime_setting

    return max(2, int(get_runtime_setting("observer_live_refresh_seconds") or 5))


def _observer_refresh_seconds() -> int:
    """Token totals / hunt snapshot. Floor at 10s so 0 cannot hammer Postgres."""
    from agent_crm.runtime_settings_store import get_runtime_setting

    return max(10, int(get_runtime_setting("observer_refresh_seconds") or 600))


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


def _on_agent_enabled_change(agent_name: str) -> None:
    key = f"agent_enabled_{agent_name}"
    set_agent_enabled(agent_name, bool(st.session_state.get(key)))


def _status_label(status: str, *, enabled: bool) -> str:
    if not enabled:
        return "⚫ paused"
    try:
        parsed = AgentStatus(status)
    except ValueError:
        parsed = AgentStatus.IDLE
    return f"{_STATUS_EMOJI.get(parsed, '⚪')} {status}"


def _format_heartbeat(value: object) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _render_agent_roster(rows: list[dict]) -> None:
    st.markdown(
        """
        <style>
        div[data-testid="stToggle"] { min-height: 0; padding-top: 0.15rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    header = st.columns(_ROSTER_COL_WEIGHTS)
    for column, title in zip(header, _ROSTER_HEADERS, strict=True):
        column.markdown(f"**{title}**")

    for row in rows:
        name = str(row.get("name") or "")
        enabled = bool(row.get("enabled", True))
        key = f"agent_enabled_{name}"
        if key not in st.session_state:
            st.session_state[key] = enabled
        cols = st.columns(_ROSTER_COL_WEIGHTS)
        cols[0].toggle(
            f"Enable {row.get('display_name') or name}",
            key=key,
            label_visibility="collapsed",
            help=f"Pause or resume {row.get('display_name') or name}",
            on_change=_on_agent_enabled_change,
            args=(name,),
        )
        cols[1].write(row.get("display_name") or name)
        cols[2].write(_status_label(str(row.get("status") or "idle"), enabled=enabled))
        cols[3].write(row.get("task") or "—")
        cols[4].write(row.get("resource") or "—")
        cols[5].write(
            _format_in_out_tokens(
                int(row.get("prompt_tokens") or 0),
                int(row.get("completion_tokens") or 0),
            )
        )
        cols[6].write(_format_token_rate(float(row.get("tokens_per_hour") or 0.0)))
        cols[7].write(_format_usd(float(row.get("saved_usd") or 0.0)))
        cols[8].write(_format_heartbeat(row.get("last_heartbeat")))


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
            "name": row.name,
            "display_name": row.display_name,
            "enabled": row.enabled,
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

    _render_agent_roster(rows)
    st.caption(
        "Flip the switch to pause or resume a standing worker from this page. "
        "Off takes effect between jobs — no Docker or CLI restart needed."
    )

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


def _render_research_tab() -> None:
    st.subheader("Research findings")
    st.caption(
        "Competitor, nonprofit, ad-placement, and retail target-company prospecting. "
        "The query queue is append-only: SearXNG/Firecrawl pages enqueue new search "
        "terms and rows are never deleted. tactic.studio target-company findings "
        "enqueue hunter searches for VPs of marketing/sales and marketing managers."
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
        options=["all", "competitor", "nonprofit", "ad_placement", "target_company", "other"],
        index=0,
    )

    brand = Brand(brand_filter) if brand_filter != "all" else None
    kind = ResearchFindingKind(kind_filter) if kind_filter != "all" else None
    findings_df = _research_finding_rows(brand=brand, kind=kind, limit=500)

    if findings_df.empty:
        st.info(
            "No findings yet. Run `agent-crm research --brand celestial-nexus` "
            "or POST to /research."
        )
        return

    st.metric("Findings", len(findings_df))
    _render_full_csv_export(
        key="research_findings",
        filename=_export_filename(
            "research-findings",
            brand.value if brand else None,
            kind.value if kind else None,
        ),
        fetch_all=lambda: _research_finding_rows(
            brand=brand, kind=kind, limit=None, truncate=False
        ),
        preview_count=len(findings_df),
        preview_cap=500,
        filter_key=(brand_filter, kind_filter),
    )
    st.dataframe(findings_df, use_container_width=True, hide_index=True)


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

    _render_full_csv_export(
        key="contact_profiles",
        filename=_export_filename(
            "contacts",
            brand.value if brand else None,
            audience.value if audience else None,
            None if quality == "all" else quality,
        ),
        fetch_all=lambda: _contact_profile_rows(
            brand=brand,
            audience=audience,
            quality=quality,
            limit=None,
        ),
        preview_count=len(profiles),
        filter_key=filter_key,
        caption=(
            f"Table shows {CONTACTS_PAGE_SIZE} per page. "
            f"Full export includes all {total} matching rows."
        ),
    )

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

    _render_full_csv_export(
        key=f"{key_prefix}comment_people",
        filename=_export_filename(
            "comment-authors",
            brand.value if brand else None,
            audience.value if audience else None,
        ),
        fetch_all=lambda: _comment_people_rows(
            brand=brand, audience=audience, limit=None
        ),
        preview_count=len(people),
        filter_key=(
            brand.value if brand else "all",
            audience.value if audience else "all",
            key_prefix,
        ),
        caption=(
            f"Table shows {page_size} per page. "
            f"Full export includes all {total} comment authors."
        ),
    )

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

    rows_df = _verifier_rows(limit=500)
    st.metric("Hunter leads", len(rows_df))

    if rows_df.empty:
        st.info("No hunter leads yet. Run `agent-crm hunt` first.")
    else:
        _render_full_csv_export(
            key="verifier_leads",
            filename=_export_filename("verifier-hunter-leads"),
            fetch_all=lambda: _verifier_rows(limit=None),
            preview_count=len(rows_df),
            preview_cap=500,
        )
        st.dataframe(rows_df, use_container_width=True, hide_index=True)

    st.caption(
        "CLI: `agent-crm verify --lead-id N` or `agent-crm verify --unverified`. "
        "Contact title/org enrichment runs separately via public-web search on the Contacts tab."
    )


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
    from agent_crm.treg_client import TregClient, TregError, treg_configured
    from agent_crm.treg_queue import allow_treg_tools, enqueue_free_treg_tools
    from agent_crm.treg_store import list_treg_tools, sync_treg_catalog, treg_counts

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


def _render_command_tab() -> None:
    st.subheader("Command")
    st.caption(
        "Send natural-language instructions to the orchestrator. It can pause or resume "
        "standing agents and enqueue hunt, research, engagement, SEO, or AEO/GEO work. "
        "Responses appear here once the orchestrator picks up your message (usually within a few seconds)."
    )

    requests = list_agency_requests(limit=80)
    pending = any(
        row.status in (AgencyRequestStatus.PENDING, AgencyRequestStatus.PROCESSING)
        for row in requests
    )
    if pending:
        st.info("Orchestrator is processing your request…")

    if not requests:
        st.info("No commands yet. Ask the orchestrator to pause agents or queue new work.")

    for row in requests:
        with st.chat_message("user"):
            st.markdown(row.message)
            st.caption(row.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
        if row.status == AgencyRequestStatus.COMPLETED:
            with st.chat_message("assistant"):
                st.markdown(row.reply or "Done.")
                if row.actions:
                    with st.expander("Actions taken"):
                        st.json(row.actions)
        elif row.status == AgencyRequestStatus.FAILED:
            with st.chat_message("assistant"):
                st.error(row.error_message or "Orchestrator could not handle that command.")
        elif row.status in (
            AgencyRequestStatus.PENDING,
            AgencyRequestStatus.PROCESSING,
        ):
            with st.chat_message("assistant"):
                st.caption("Queued — waiting for orchestrator…")

    prompt = st.chat_input("Message the orchestrator…")
    if prompt:
        create_agency_request(prompt)
        st.rerun()


def _command_fragment() -> None:
    try:
        fragment = st.fragment(run_every=timedelta(seconds=5))
    except TypeError:
        fragment = st.fragment

    @fragment
    def _command() -> None:
        _render_command_tab()

    _command()


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

    notes_df = _improvement_note_rows(limit=200)
    _render_full_csv_export(
        key="improvement_notes",
        filename=_export_filename("improvement-notes"),
        fetch_all=lambda: _improvement_note_rows(limit=None),
        preview_count=len(notes_df),
        preview_cap=200,
    )
    st.dataframe(
        notes_df,
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
    st.title("The Agency")
    st.caption("This dashboard is password-protected.")
    entered = st.text_input("Password", type="password", key="dashboard_password_input")
    if st.button("Unlock", type="primary"):
        if hmac.compare_digest(entered, expected):
            st.session_state.dashboard_unlocked = True
            st.rerun()
        st.error("Invalid password")
    return False


def main() -> None:
    st.set_page_config(page_title="The Agency", layout="wide")
    _disable_stale_fade()
    init_db()
    if not _require_dashboard_access():
        return

    live_seconds = _observer_live_refresh_seconds()
    refresh_seconds = _observer_refresh_seconds()

    st.title("The Agency")
    st.caption(f"Store: {database_kind()} · CRM + SEO + AEO/GEO documents")

    observer_tab, command_tab, settings_tab, pipeline_tab, hunter_tab, research_tab, engagement_tab, seo_tab, aeo_geo_tab, contacts_tab, verifier_tab, improvement_tab = st.tabs(
        [
            "Live agents",
            "Command",
            "Settings",
            "Pipeline & leads",
            "Hunter",
            "Research",
            "Engagement",
            "SEO",
            "AEO / GEO",
            "Contacts",
            "Verifier",
            "Improvement",
        ]
    )

    with observer_tab:
        _observer_fragment(live_seconds=live_seconds, token_seconds=refresh_seconds)

    with command_tab:
        _command_fragment()

    with settings_tab:
        _render_settings_tab()

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

    with aeo_geo_tab:
        _render_aeo_geo_tab()

    with contacts_tab:
        _render_contacts_tab()

    with verifier_tab:
        _render_verifier_tab()

    with improvement_tab:
        _render_improvement_tab()


if __name__ == "__main__":
    main()
