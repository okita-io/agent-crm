"""Dashboard UI module: tabs.improvement."""
from __future__ import annotations


import pandas as pd
import streamlit as st

from agent_crm.improvement_store import count_open_improvement_notes, list_improvement_notes

from agent_crm.dashboard_ui.common import (
    _export_filename,
    _render_full_csv_export,
)

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

