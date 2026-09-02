"""Dashboard UI module: tabs.observer."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import streamlit as st

from agent_crm.agent_control import set_agent_enabled
from agent_crm.enums import AgencyRequestStatus, AgentStatus, Brand, ContactAudience, ContactVerificationStatus, HuntResourceKind, LeadSource, ResearchFindingKind, SeoPlanKind, SeoReviewKind, Stage
from agent_crm.heartbeat import list_heartbeats
from agent_crm.presence import build_observer_rows, fetch_spark_queue_health, spark_slot_summary

from agent_crm.dashboard_ui.common import (
    _cached_token_snapshot,
    _render_catalog_growth,
    _render_hunt_loop_status,
    _render_live_refresh_bar,
)

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


def _format_heartbeat(value: object) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _status_label(status: str, *, enabled: bool) -> str:
    if not enabled:
        return "⚫ paused"
    try:
        parsed = AgentStatus(status)
    except ValueError:
        parsed = AgentStatus.IDLE
    return f"{_STATUS_EMOJI.get(parsed, '⚪')} {status}"


def _on_agent_enabled_change(agent_name: str) -> None:
    key = f"agent_enabled_{agent_name}"
    set_agent_enabled(agent_name, bool(st.session_state.get(key)))


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
    _render_catalog_growth(compact=True)

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


def _observer_fragment(*, live_seconds: int, token_seconds: int) -> None:
    try:
        fragment = st.fragment(run_every=timedelta(seconds=live_seconds))
    except TypeError:
        fragment = st.fragment

    @fragment
    def _observer() -> None:
        _render_agent_observer(live_seconds=live_seconds, token_seconds=token_seconds)

    _observer()

