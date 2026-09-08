"""API tests for orchestrator command queue."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import AgencyRequestStatus


def test_agency_request_round_trip(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "agency-api.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()

    client = TestClient(app)
    created = client.post("/agency/requests", json={"message": "  Pause hunter  "})
    assert created.status_code == 200
    payload = created.json()
    assert payload["message"] == "Pause hunter"
    assert payload["status"] == AgencyRequestStatus.PENDING.value
    assert payload["reply"] is None

    listed = client.get("/agency/requests")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["id"] == payload["id"]
    assert rows[0]["message"] == "Pause hunter"

    blank = client.post("/agency/requests", json={"message": "   "})
    assert blank.status_code == 422

    reset_engine()
    get_settings.cache_clear()
