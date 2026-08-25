"""API tests for hunter endpoints."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_crm.api import app


def test_hunt_endpoint(db_url):
    client = TestClient(app)
    with patch("agent_crm.api.OutboundHunter") as mock_cls:
        mock_cls.return_value.hunt_once.return_value = {
            "query": "test",
            "brand": "midnightsatin",
            "results_count": 1,
            "resources_collected": 1,
            "pages_scraped": 0,
            "leads_created": 0,
            "params": {},
        }
        response = client.post(
            "/hunt",
            json={"query": "test", "brand": "midnightsatin"},
        )
    assert response.status_code == 200
    assert response.json()["results_count"] == 1


def test_hunt_resources_list(db_url):
    from agent_crm.enums import Brand
    from agent_crm.hunt_store import HuntStore

    store = HuntStore()
    store.upsert_resource(
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


def test_hunt_queue_status(db_url):
    from agent_crm.enums import Brand
    from agent_crm.hunt_store import HuntStore

    HuntStore().enqueue_query(query="pending one", brand=Brand.MIDNIGHTSATIN, origin="seed")

    client = TestClient(app)
    response = client.get("/hunt/queue")
    assert response.status_code == 200
    assert response.json()["pending"] >= 1
