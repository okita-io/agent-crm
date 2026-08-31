"""Tests for agent heartbeat persistence and API."""

from __future__ import annotations

import enum

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import dialect as pg_dialect

from agent_crm.api import app
from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import (
    ActivityType,
    AgentStatus,
    Brand,
    JourneyStatus,
    LeadSource,
    LeadStatus,
    Priority,
    Stage,
)
from agent_crm.heartbeat import list_heartbeats, record_heartbeat
from agent_crm.models import Activity, AgentHeartbeat, Journey, Lead, Opportunity, str_enum


def _postgres_bind(enum_type, member: enum.Enum) -> object:
    processor = enum_type.bind_processor(pg_dialect())
    assert processor is not None
    return processor(member)


@pytest.mark.parametrize(
    ("enum_cls", "member"),
    [
        (AgentStatus, AgentStatus.THINKING),
        (AgentStatus, AgentStatus.WORKING),
        (AgentStatus, AgentStatus.BLOCKED),
        (AgentStatus, AgentStatus.IDLE),
    ],
)
def test_agent_status_postgres_bind_uses_enum_values(
    enum_cls: type[enum.Enum],
    member: enum.Enum,
) -> None:
    """Agent heartbeats must persist lowercase values for the Postgres agentstatus enum."""
    enum_type = str_enum(enum_cls)
    bound = _postgres_bind(enum_type, member)
    assert bound == member.value
    assert bound != member.name


def test_agent_heartbeat_model_column_persists_values() -> None:
    """The mapped AgentHeartbeat.status column must bind enum values for Postgres."""
    status_type = AgentHeartbeat.__mapper__.columns["status"].type
    bound = _postgres_bind(status_type, AgentStatus.THINKING)
    assert bound == "thinking"
    assert bound != "THINKING"


@pytest.mark.parametrize(
    ("model", "column", "enum_cls", "member"),
    [
        (Lead, "source", LeadSource, LeadSource.HUNTER),
        (Lead, "brand", Brand, Brand.MIDNIGHTSATIN),
        (Lead, "status", LeadStatus, LeadStatus.ACTIVE),
        (Lead, "priority", Priority, Priority.HIGH),
        (Opportunity, "stage", Stage, Stage.PROSPECT),
        (Activity, "type", ActivityType, ActivityType.SCRAPE),
        (Journey, "status", JourneyStatus, JourneyStatus.PAUSED),
    ],
)
def test_legacy_enums_postgres_bind_uses_member_names(
    model,
    column: str,
    enum_cls: type[enum.Enum],
    member: enum.Enum,
) -> None:
    """Initial Alembic schema created Postgres enums from member names (e.g. HUNTER)."""
    from sqlalchemy import Enum as SAEnum

    enum_type = SAEnum(enum_cls)
    bound = _postgres_bind(enum_type, member)
    assert bound == member.name
    assert bound != member.value


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "heartbeat.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("CRM_API_TOKEN", "")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield TestClient(app)
    reset_engine()
    get_settings.cache_clear()


def test_record_and_list_heartbeat(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "heartbeat-memory.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
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
    assert all("prompt_tokens" in row and "completion_tokens" in row for row in agents)
    assert all("tokens_per_hour" in row for row in agents)
    assert all("enabled" in row for row in agents)


def test_agent_toggle_api_round_trip(client: TestClient) -> None:
    listed = client.get("/agents/toggles")
    assert listed.status_code == 200
    assert any(row["agent_name"] == "seo" for row in listed.json())

    disabled = client.put("/agents/seo/enabled", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json() == {"agent_name": "seo", "enabled": False}

    agents = client.get("/agents")
    seo_row = next(row for row in agents.json() if row["name"] == "seo")
    assert seo_row["enabled"] is False

    heartbeat = client.post(
        "/agents/seo/heartbeat",
        json={"status": "idle", "task": "paused"},
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["task"] == "paused"

    enabled = client.put("/agents/seo/enabled", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
