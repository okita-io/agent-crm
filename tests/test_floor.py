"""Live Agents floor queue aggregation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.floor import build_queue_lanes


def test_build_queue_lanes_empty(db_url) -> None:
    payload = build_queue_lanes()
    assert payload["waiting"] == 0
    ids = [lane["id"] for lane in payload["lanes"]]
    assert ids == [
        "research",
        "engagement",
        "hunter",
        "queue-review",
        "seo",
        "jobs",
    ]
    assert all(lane["pending"] == 0 for lane in payload["lanes"])


def test_queues_endpoint(db_url) -> None:
    client = TestClient(app)
    response = client.get("/queues")
    assert response.status_code == 200
    body = response.json()
    assert body["waiting"] == 0
    assert len(body["lanes"]) == 6


def test_spark_endpoint_shape(db_url) -> None:
    client = TestClient(app)
    response = client.get("/agents/spark")
    assert response.status_code == 200
    body = response.json()
    assert body["max_concurrency"] >= 1
    assert "in_flight" in body
    assert "waiters" in body


def test_cors_preflight_allows_vite(db_url) -> None:
    client = TestClient(app)
    response = client.options(
        "/agents",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
