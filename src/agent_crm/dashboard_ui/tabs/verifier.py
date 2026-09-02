"""Dashboard UI module: tabs.verifier."""
from __future__ import annotations


import pandas as pd
import streamlit as st

from agent_crm.enums import AgencyRequestStatus, AgentStatus, Brand, ContactAudience, ContactVerificationStatus, HuntResourceKind, LeadSource, ResearchFindingKind, SeoPlanKind, SeoReviewKind, Stage
from agent_crm.tooling import CRMToolkit
from agent_crm.contacts.verifier import list_verifications

from agent_crm.dashboard_ui.common import (
    _export_filename,
    _render_full_csv_export,
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

