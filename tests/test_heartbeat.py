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


def test_record_heartbeat_clips_long_task_and_resource(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "heartbeat-clip.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()

    query = (
        "searching: Compile a comprehensive list of potential client companies "
        "for tactic-studio: major US/UK food and beverage brands and sports "
        "clothing brands. Include company names, websites, locations, category, "
        "and why they fit as potential clients. Also provide a count of "
        "qualifying companies."
    )
    assert len(query) > 255
    snapshot = record_heartbeat(
        "research",
        status=AgentStatus.THINKING,
        task=query,
        resource="https://example.com/" + ("a" * 300),
    )
    assert snapshot.task is not None
    assert len(snapshot.task) == 255
    assert snapshot.task.endswith("…")
    assert snapshot.resource is not None
    assert len(snapshot.resource) == 255
    stored = list_heartbeats()
    assert stored[0].task == snapshot.task
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
    assert all(row["enabled"] is True for row in agents)
    research = next(row for row in agents if row["name"] == "research")
    assert research["placeholder"] is False
    assert research["toggleable"] is True
    assert "marketing-agi" in research["skills"]
    intake = next(row for row in agents if row["name"] == "lead_intake")
    assert intake["placeholder"] is True
    assert intake["toggleable"] is False


def test_agent_enabled_api_round_trip(client: TestClient) -> None:
    off = client.put("/agents/outbound_hunter/enabled", json={"enabled": False})
    assert off.status_code == 200
    assert off.json() == {"name": "outbound_hunter", "enabled": False}

    listed = {row["name"]: row for row in client.get("/agents").json()}
    assert listed["outbound_hunter"]["enabled"] is False
    assert listed["outbound_hunter"]["task"] == "paused"

    on = client.put("/agents/outbound_hunter/enabled", json={"enabled": True})
    assert on.status_code == 200
    assert on.json()["enabled"] is True
