"""Live Agents floor queue aggregation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.engagement.query_store import EngagementQueryStore
from agent_crm.enums import (
    AgentJobKind,
    Brand,
    ResearchFindingKind,
    SeoQueryKind,
)
from agent_crm.floor import build_queue_lanes
from agent_crm.hunt.store import HuntStore
from agent_crm.jobs.store import enqueue_job
from agent_crm.research.query_store import ResearchQueryStore
from agent_crm.seo.query_store import SeoQueryStore

_EMPTY_LANE_IDS = [
    "research",
    "hunter",
    "engagement",
    "queue-review",
    "seo",
    "aeo-geo",
    "jobs",
]


def test_build_queue_lanes_empty(db_url) -> None:
    payload = build_queue_lanes()
    assert payload["waiting"] == 0
    ids = [lane["id"] for lane in payload["lanes"]]
    assert ids == _EMPTY_LANE_IDS
    assert all(lane["pending"] == 0 for lane in payload["lanes"])
    assert all(lane["running"] == 0 for lane in payload["lanes"])
    assert all(lane["prompts"] == [] for lane in payload["lanes"])


def test_build_queue_lanes_shows_prompt_titles(db_url) -> None:
    ResearchQueryStore().enqueue_query(
        query="natal chart competitor landscape",
        brand=Brand.CELESTIAL_NEXUS,
        kind=ResearchFindingKind.COMPETITOR,
        origin="seed",
    )
    HuntStore().enqueue_query(
        query="reddit: astrology apps worth using",
        brand=Brand.CELESTIAL_NEXUS,
        origin="seed",
    )
    HuntStore().enqueue_query(
        query="follow-up natal chart youtuber",
        brand=Brand.CELESTIAL_NEXUS,
        origin="person:hunter",
    )
    EngagementQueryStore().enqueue_query(
        query="site:reddit.com natal chart app recs",
        brand=Brand.CELESTIAL_NEXUS,
        origin="seed",
    )
    SeoQueryStore().enqueue_query(
        query="site audit celestial nexus",
        brand=Brand.CELESTIAL_NEXUS,
        kind=SeoQueryKind.SITE_AUDIT,
    )
    SeoQueryStore().enqueue_query(
        query="GEO review for chart landing pages",
        brand=Brand.CELESTIAL_NEXUS,
        kind=SeoQueryKind.AEO_GEO,
    )
    enqueue_job(
        kind=AgentJobKind.VERIFY_LEAD,
        dedupe_key="verify_lead:test-floor",
        payload={"lead_id": 1},
    )

    payload = build_queue_lanes()
    by_id = {lane["id"]: lane for lane in payload["lanes"]}

    assert by_id["research"]["pending"] == 1
    assert by_id["research"]["prompts"] == ["natal chart competitor landscape"]
    assert by_id["research"]["oldest_wait_seconds"] is not None

    assert by_id["hunter"]["pending"] == 1
    assert "reddit: astrology apps worth using" in by_id["hunter"]["prompts"]

    assert by_id["engagement"]["pending"] == 1
    assert by_id["engagement"]["prompts"] == ["site:reddit.com natal chart app recs"]

    assert by_id["queue-review"]["pending"] == 1
    assert by_id["queue-review"]["prompts"] == ["person: follow-up natal chart youtuber"]

    assert by_id["seo"]["pending"] == 1
    assert by_id["seo"]["prompts"] == ["site audit celestial nexus"]
    assert by_id["aeo-geo"]["pending"] == 1
    assert by_id["aeo-geo"]["prompts"] == ["GEO review for chart landing pages"]

    assert by_id["jobs"]["pending"] == 1
    assert by_id["jobs"]["prompts"] == ["verify_lead × 1"]

    assert payload["waiting"] == 7
    assert [lane["id"] for lane in payload["lanes"]] == _EMPTY_LANE_IDS


def test_queues_endpoint(db_url) -> None:
    client = TestClient(app)
    response = client.get("/queues")
    assert response.status_code == 200
    body = response.json()
    assert body["waiting"] == 0
    assert len(body["lanes"]) == 7
    assert "running" in body["lanes"][0]
    assert "oldest_wait_seconds" in body["lanes"][0]


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
