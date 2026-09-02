"""Tests for self-learning improvement notes and orchestrator helpers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.enums import (
    AgentJobKind,
    ImprovementNoteKind,
    ImprovementNoteSeverity,
    ImprovementNoteStatus,
    ImprovementSourceAgent,
)
from agent_crm.improvement_store import (
    list_improvement_notes,
    make_fingerprint,
    record_improvement_note,
)
from agent_crm.agency.orchestrator import note_job_failure, run_orchestrator_cycle

pytestmark = pytest.mark.usefixtures("db_url")


@pytest.fixture()
def api_client() -> TestClient:
    return TestClient(app)


def test_record_improvement_note_dedupes_open_fingerprint() -> None:
    fingerprint = make_fingerprint("gap", "test-dedupe")
    first = record_improvement_note(
        kind=ImprovementNoteKind.GAP,
        severity=ImprovementNoteSeverity.WARN,
        source_agent=ImprovementSourceAgent.ORCHESTRATOR,
        title="Test gap",
        body="Initial body",
        fingerprint=fingerprint,
    )
    second = record_improvement_note(
        kind=ImprovementNoteKind.GAP,
        severity=ImprovementNoteSeverity.CRITICAL,
        source_agent=ImprovementSourceAgent.ORCHESTRATOR,
        title="Test gap updated",
        body="Updated body",
        fingerprint=fingerprint,
    )
    assert first == second
    notes = list_improvement_notes(status=ImprovementNoteStatus.OPEN)
    assert len(notes) == 1
    assert notes[0].title == "Test gap updated"
    assert notes[0].severity == ImprovementNoteSeverity.CRITICAL


def test_note_job_failure_records_activitytype_repair() -> None:
    note_job_failure(
        kind=AgentJobKind.VERIFY_LEAD,
        job_id=99,
        error_text=(
            '(psycopg.errors.InvalidTextRepresentation) invalid input value for '
            'enum activitytype: "VERIFIED"'
        ),
    )
    notes = list_improvement_notes(status=ImprovementNoteStatus.OPEN)
    assert any("activitytype" in note.title.lower() for note in notes)
    assert any(note.kind == ImprovementNoteKind.REPAIR for note in notes)


def test_orchestrator_cycle_runs_without_error() -> None:
    run_orchestrator_cycle()
    notes = list_improvement_notes(status=None, limit=50)
    assert isinstance(notes, list)


def test_improvement_notes_api(api_client: TestClient) -> None:
    record_improvement_note(
        kind=ImprovementNoteKind.GAP,
        severity=ImprovementNoteSeverity.INFO,
        source_agent=ImprovementSourceAgent.ORCHESTRATOR,
        title="API test note",
        body="Visible via API",
        fingerprint=make_fingerprint("api", "test"),
    )
    response = api_client.get("/improvement-notes?status=open")
    assert response.status_code == 200
    payload = response.json()
    assert any(row["title"] == "API test note" for row in payload)
