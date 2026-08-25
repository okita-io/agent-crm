"""Tests for agent heartbeat persistence and API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import AgentStatus
from agent_crm.heartbeat import list_heartbeats, record_heartbeat


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "heartbeat.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()
    init_db()
    yield TestClient(app)
    reset_engine()


def test_record_and_list_heartbeat(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "heartbeat-memory.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()
    init_db()

    snapshot = record_heartbeat(
        "orchestrator",
        status=AgentStatus.THINKING,
        task="dispatch lead 3",
        resource="scheduler",
    )
    assert snapshot.agent_name == "orchestrator"
    assert snapshot.status == AgentStatus.THINKING
    assert snapshot.task == "dispatch lead 3"

    stored = list_heartbeats()
    assert len(stored) == 1
    assert stored[0].status == AgentStatus.THINKING
    reset_engine()


def test_heartbeat_api_round_trip(client: TestClient) -> None:
    response = client.post(
        "/agents/orchestrator/heartbeat",
        json={
            "status": "working",
            "task": "lead 12",
            "resource": "spark-queue",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_name"] == "orchestrator"
    assert payload["status"] == "working"
    assert payload["task"] == "lead 12"


def test_list_agents_includes_idle_roster(client: TestClient) -> None:
    response = client.get("/agents")
    assert response.status_code == 200
    agents = response.json()
    names = {row["name"] for row in agents}
    assert "lead_intake" in names
    assert "orchestrator" in names
    assert all(row["status"] in {"idle", "thinking", "working", "blocked"} for row in agents)
