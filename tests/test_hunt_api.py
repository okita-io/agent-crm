"""API tests for hunt loop endpoints."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import Brand
from agent_crm.hunt_store import HuntStore


def test_hunt_loop_endpoint(tmp_path, monkeypatch):
    db_path = tmp_path / "api-loop.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()

    client = TestClient(app)
    with patch("agent_crm.api.run_hunt_loop") as mock_run:
        from agent_crm.hunt_loop import HuntLoopResult

        mock_run.return_value = HuntLoopResult(
            run_id="abc",
            queries_run=2,
            resources_found=5,
            branch_terms_enqueued=1,
            stop_reason="max_queries",
        )
        response = client.post(
            "/hunt/loop",
            json={"brand": "midnightsatin", "max_queries": 2, "max_minutes": 5},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["queries_run"] == 2
    assert payload["resources_found"] == 5
    reset_engine()
    get_settings.cache_clear()


def test_hunt_loop_rejects_unlimited_without_flag(tmp_path, monkeypatch):
    db_path = tmp_path / "api-loop-unlimited.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    client = TestClient(app)
    response = client.post(
        "/hunt/loop",
        json={"brand": "midnightsatin", "max_queries": 0, "max_minutes": 0},
    )
    assert response.status_code == 400
    reset_engine()
    get_settings.cache_clear()


def test_api_token_required_when_configured(tmp_path, monkeypatch):
    db_path = tmp_path / "api-token.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("CRM_API_TOKEN", "secret-token")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/contacts").status_code == 401
    ok = client.get("/contacts", headers={"X-CRM-Token": "secret-token"})
    assert ok.status_code == 200
    reset_engine()
    get_settings.cache_clear()


def test_hunt_resources_list(tmp_path, monkeypatch):
    db_path = tmp_path / "api-resources.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()

    HuntStore().upsert_resource(
        url="https://example.com/community",
        brand=Brand.HEYBUDDY,
        title="AI Companion Forum",
        found_via_query="ai communities",
    )

    client = TestClient(app)
    response = client.get("/hunt/resources", params={"brand": "heybuddy"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["domain"] == "example.com"
    reset_engine()
    get_settings.cache_clear()
