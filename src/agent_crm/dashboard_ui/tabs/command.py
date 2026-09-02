"""Dashboard UI module: tabs.command."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import streamlit as st

from agent_crm.agency_request_store import create_agency_request, list_agency_requests
from agent_crm.enums import AgencyRequestStatus, AgentStatus, Brand, ContactAudience, ContactVerificationStatus, HuntResourceKind, LeadSource, ResearchFindingKind, SeoPlanKind, SeoReviewKind, Stage

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

