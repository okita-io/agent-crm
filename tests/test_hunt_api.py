"""API tests for hunt loop endpoints."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import Brand, HuntQueryStatus
from agent_crm.hunt.store import HuntStore


def test_hunt_loop_endpoint(tmp_path, monkeypatch):
    db_path = tmp_path / "api-loop.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()

    client = TestClient(app)
    with patch("agent_crm.api.run_hunt_loop") as mock_run:
        from agent_crm.hunt.loop import HuntLoopResult

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


def test_hunt_query_inspector_list_toss_retry(tmp_path, monkeypatch):
    db_path = tmp_path / "api-hunt-inspector.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()

    store = HuntStore()
    store.enqueue_query(
        query="retail VP of marketing",
        brand=Brand.TACTIC_STUDIO,
        origin="seed_pack",
    )
    store.enqueue_query(
        query="nested branch noise",
        brand=Brand.TACTIC_STUDIO,
        origin="branch:branch:marketing:seed_pack",
    )
    store.enqueue_query(
        query="failed romance query",
        brand=Brand.MIDNIGHTSATIN,
        origin="community:reddit",
    )
    failed = store.list_queries(q="failed romance", limit=1)[0]
    store.mark_query_failed(failed.id, "nul bytes")
    review_row = next(
        row for row in store.list_queries(limit=10) if "nested branch" in row.query
    )
    assert review_row.status == HuntQueryStatus.PENDING_REVIEW

    client = TestClient(app)
    listed = client.get(
        "/hunt/queries",
        params={"status": "pending", "drain_order": True},
    )
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] >= 1
    assert any(item["query"] == "retail VP of marketing" for item in payload["items"])

    keep = client.post(f"/hunt/queries/{review_row.id}/keep")
    assert keep.status_code == 200
    assert keep.json()["status"] == "pending"

    tossed = client.post(
        "/hunt/queries/reject-matching",
        json={"status": "pending", "origin_prefix": "branch", "reason": "test toss branch"},
    )
    assert tossed.status_code == 200
    assert tossed.json()["rejected"] >= 1

    retry = client.post(f"/hunt/queries/{failed.id}/retry")
    assert retry.status_code == 200
    assert retry.json()["status"] in {"pending", "pending_review"}

    blocked = client.post(
        "/hunt/queries/reject-matching",
        json={"status": "pending", "reason": "too broad"},
    )
    assert blocked.status_code == 422

    running = store.list_queries(q="retail VP", limit=1)[0]
    store.mark_query_running(running.id)
    cleared = client.post("/hunt/queries/clear", json={"reason": "operator clear"})
    assert cleared.status_code == 200
    assert cleared.json()["rejected"] >= 1
    leftover = {row.status for row in store.list_queries(limit=20)}
    assert HuntQueryStatus.PENDING not in leftover
    assert HuntQueryStatus.FAILED not in leftover
    assert HuntQueryStatus.RUNNING in leftover

    reset_engine()
    get_settings.cache_clear()
