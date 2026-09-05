"""Dashboard UI module: tabs.engagement + publish schedule."""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from agent_crm.config import get_settings
from agent_crm.dashboard_ui.common import (
    _clip,
    _export_filename,
    _render_full_csv_export,
)
from agent_crm.engagement.query_store import EngagementQueryStore
from agent_crm.engagement.store import count_drafts, count_threads, list_drafts, list_threads
from agent_crm.enums import Brand, EngagementDraftStatus, PublishJobStatus, SocialPlatform
from agent_crm.publish.schedule import ScheduleError, schedule_engagement_drafts
from agent_crm.publish.store import (
    create_social_account,
    list_publish_jobs,
    list_social_accounts,
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
                "id": row.id,
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


def _publish_job_rows(brand: Brand | None, *, limit: int = 200) -> pd.DataFrame:
    jobs = list_publish_jobs(brand=brand, limit=limit)
    if not jobs:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "id": row.id,
                "status": row.status.value,
                "brand": row.brand.value,
                "platform": row.platform.value,
                "account_id": row.account_id,
                "scheduled_at": row.scheduled_at,
                "dry_run": row.dry_run,
                "posted_url": row.posted_url,
                "error": row.error,
                "body": _clip(row.body, 200),
            }
            for row in jobs
        ]
    )


def _render_engagement_tab() -> None:
    st.subheader("Agent engagement")
    st.caption(
        "High-traffic forums and popular threads catalogued for comment drafts. "
        "The query queue is append-only. Draft agents do not publish — approve and "
        "schedule below; the standing publisher worker sends after the slot."
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

    st.subheader("Draft replies")
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

    st.subheader("Approve & schedule")
    settings = get_settings()
    st.caption(
        f"Publisher dry-run is **{'on' if settings.publish_dry_run else 'off'}** "
        f"(CRM_PUBLISH_DRY_RUN). tactic.studio stays Pete-gated."
    )

    accounts = list_social_accounts(brand=brand, enabled_only=True, limit=100)
    with st.expander("Add social account"):
        with st.form("add_social_account"):
            acct_brand = st.selectbox(
                "Brand",
                options=[b.value for b in Brand if b != Brand.UNASSIGNED],
                key="new_acct_brand",
            )
            acct_platform = st.selectbox(
                "Platform",
                options=[p.value for p in SocialPlatform],
                key="new_acct_platform",
            )
            acct_handle = st.text_input("Handle", key="new_acct_handle")
            acct_postiz = st.text_input(
                "Postiz integration id (owned feeds)", key="new_acct_postiz"
            )
            acct_cred = st.text_input(
                "Credential key (CRM_SOCIAL_{KEY}_*)", key="new_acct_cred"
            )
            acct_cap = st.number_input("Daily cap", min_value=1, max_value=50, value=3)
            acct_interval = st.number_input(
                "Min interval (minutes)", min_value=1, max_value=10080, value=240
            )
            if st.form_submit_button("Create account"):
                try:
                    create_social_account(
                        brand=Brand(acct_brand),
                        platform=SocialPlatform(acct_platform),
                        handle=acct_handle,
                        postiz_integration_id=acct_postiz or None,
                        credential_key=acct_cred or None,
                        daily_cap=int(acct_cap),
                        min_interval_minutes=int(acct_interval),
                    )
                    st.success("Account created")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))

    if not accounts:
        st.warning("Create a social account before scheduling.")
    else:
        schedulable = [
            d
            for d in list_drafts(brand=brand, limit=200)
            if d.status
            in (
                EngagementDraftStatus.DRAFT,
                EngagementDraftStatus.REVIEW,
                EngagementDraftStatus.APPROVED,
            )
        ]
        draft_options = {
            f"#{d.id} [{d.brand.value}] { _clip(d.draft_text, 80) }": d.id
            for d in schedulable
        }
        selected_labels = st.multiselect(
            "Drafts to schedule",
            options=list(draft_options.keys()),
            key="schedule_draft_labels",
        )
        account_labels = {
            f"#{a.id} {a.brand.value}/{a.platform.value} @{a.handle}": a.id
            for a in accounts
        }
        account_label = st.selectbox(
            "Post as", options=list(account_labels.keys()), key="schedule_account"
        )
        use_next = st.checkbox("Use next available slot", value=True, key="schedule_next")
        scheduled_at = None
        if not use_next:
            scheduled_at = st.datetime_input(
                "Schedule at (UTC)",
                value=datetime.now(UTC).replace(tzinfo=None),
                key="schedule_at",
            )
            if scheduled_at is not None and scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=UTC)
        pete = st.checkbox(
            "Pete override (tactic.studio only)",
            value=False,
            key="schedule_pete",
        )
        if st.button("Schedule selected drafts", type="primary", key="schedule_submit"):
            draft_ids = [draft_options[label] for label in selected_labels]
            if not draft_ids:
                st.error("Select at least one draft")
            else:
                try:
                    jobs = schedule_engagement_drafts(
                        draft_ids=draft_ids,
                        account_id=account_labels[account_label],
                        scheduled_at=scheduled_at,
                        use_next_slot=use_next,
                        pete_override=pete,
                    )
                    st.success(f"Scheduled {len(jobs)} job(s)")
                    st.rerun()
                except ScheduleError as exc:
                    st.error(str(exc))

    st.subheader("Publish jobs")
    jobs_df = _publish_job_rows(brand, limit=200)
    if jobs_df.empty:
        st.info("No publish jobs yet.")
    else:
        st.dataframe(jobs_df, use_container_width=True, hide_index=True)
        pending = sum(
            1
            for row in list_publish_jobs(brand=brand, limit=500)
            if row.status == PublishJobStatus.SCHEDULED
        )
        st.caption(f"{pending} scheduled job(s) waiting for publish-loop.")
