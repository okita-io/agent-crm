"""Streamlit dashboard: live agents, pipeline, and hunter resources."""

from __future__ import annotations

import hmac

import streamlit as st

from agent_crm.config import get_settings
from agent_crm.db import database_kind, init_db
from agent_crm.dashboard_ui.common import (
    _disable_stale_fade,
    _observer_live_refresh_seconds,
    _observer_refresh_seconds,
)
from agent_crm.dashboard_ui.tabs.aeo_geo import _render_aeo_geo_tab
from agent_crm.dashboard_ui.tabs.command import _command_fragment
from agent_crm.dashboard_ui.tabs.contacts import _render_contacts_tab
from agent_crm.dashboard_ui.tabs.engagement import _render_engagement_tab
from agent_crm.dashboard_ui.tabs.hunter import _render_hunter_tab
from agent_crm.dashboard_ui.tabs.improvement import _render_improvement_tab
from agent_crm.dashboard_ui.tabs.observer import _observer_fragment
from agent_crm.dashboard_ui.tabs.pipeline import _render_pipeline_tab
from agent_crm.dashboard_ui.tabs.research import _render_research_tab
from agent_crm.dashboard_ui.tabs.seo import _render_seo_tab
from agent_crm.dashboard_ui.tabs.settings import _render_settings_tab
from agent_crm.dashboard_ui.tabs.verifier import _render_verifier_tab

# Public map for dashboard contract tests (export widget keys per tab).
TAB_EXPORT_KEYS = {
    "pipeline": ["pipeline_leads"],
    "hunter": ["hunter_communities", "hunter_derived_queries", "hunter_resources"],
    "research": ["research_findings"],
    "engagement": ["engagement_threads", "engagement_drafts"],
    "seo": ["seo_targets", "seo_reviews", "seo_plans"],
    "aeo_geo": ["aeo_geo_reviews", "aeo_geo_plans"],
    "contacts": ["contact_profiles"],
    "comment_people": ["{key_prefix}comment_people"],
    "verifier": ["verifier_leads"],
    "improvement": ["improvement_notes"],
}

TAB_LABELS = (
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
)


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

    (
        observer_tab,
        command_tab,
        settings_tab,
        pipeline_tab,
        hunter_tab,
        research_tab,
        engagement_tab,
        seo_tab,
        aeo_geo_tab,
        contacts_tab,
        verifier_tab,
        improvement_tab,
    ) = st.tabs(list(TAB_LABELS))

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
