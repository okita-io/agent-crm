"""Dashboard UI module: common."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pandas as pd
import streamlit as st

from agent_crm.hunt_status import STALE_RUNNING_MINUTES, build_hunt_status, infer_hunt_phase
from agent_crm.runtime_settings_store import get_runtime_setting
from agent_crm.token_usage_store import load_token_usage_snapshot

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


def _format_refresh_interval(seconds: int) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        minutes = seconds // 60
        return "1 min" if minutes == 1 else f"{minutes} min"
    if seconds >= 60:
        minutes, remainder = divmod(seconds, 60)
        return f"{minutes}m {remainder}s"
    return f"{seconds}s"


def _observer_live_refresh_seconds() -> int:
    """Spark slots + heartbeat/status/task. Floor at 2s to avoid a busy loop."""
    from agent_crm.runtime_settings_store import get_runtime_setting

    return max(2, int(get_runtime_setting("observer_live_refresh_seconds") or 5))


def _observer_refresh_seconds() -> int:
    """Token totals / hunt snapshot. Floor at 10s so 0 cannot hammer Postgres."""
    from agent_crm.runtime_settings_store import get_runtime_setting

    return max(10, int(get_runtime_setting("observer_refresh_seconds") or 600))


_CACHE_TTL = _observer_refresh_seconds()


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

