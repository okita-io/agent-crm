"""Tests for Outbound Hunter, SearXNG, and Firecrawl clients."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import AgentStatus, Brand, LeadSource, Stage, TopicalRelevanceVerdict
from agent_crm.firecrawl_client import FirecrawlError, scrape
from agent_crm.heartbeat import list_heartbeats
from agent_crm.outbound_hunter import run_hunt
from agent_crm.schemas import HuntRequest
from agent_crm.searxng_client import SearxngError, search
from agent_crm.tooling import CRMToolkit


def _on_topic_assessment():
    assessment = MagicMock()
    assessment.verdict = TopicalRelevanceVerdict.ON_TOPIC
    assessment.reason = "test"
    return assessment


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "hunter.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()
    init_db()
    yield TestClient(app)
    reset_engine()


def _mock_transport(handlers: dict[str, callable]) -> httpx.MockTransport:
    def _dispatch(request: httpx.Request) -> httpx.Response:
        for prefix, handler in handlers.items():
            if request.url.path.startswith(prefix) or str(request.url).startswith(prefix):
                return handler(request)
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(_dispatch)


def test_searxng_search_parses_results() -> None:
    payload = {
        "results": [
            {"url": "https://example.com/a", "title": "Alpha Co", "content": "alpha"},
            {"url": "https://example.com/b", "title": "Beta LLC", "content": "beta"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert "format=json" in str(request.url)
        return httpx.Response(200, json=payload)

    http = httpx.Client(transport=_mock_transport({"/search": handler}))
    results = search("boutique studio", limit=5, client=http)
    assert len(results) == 2
    assert results[0].title == "Alpha Co"
    assert results[0].url == "https://example.com/a"


def test_searxng_search_raises_on_failure() -> None:
    http = httpx.Client(
        transport=_mock_transport(
            {"/search": lambda _r: httpx.Response(500, text="boom")}
        )
    )
    with pytest.raises(SearxngError):
        search("fail query", client=http)


def test_firecrawl_scrape_normalizes_v1_payload() -> None:
    payload = {
        "success": True,
        "data": {
            "markdown": "# Hello",
            "metadata": {"title": "Hello World"},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/scrape"
        body = json.loads(request.content.decode())
        assert body["url"] == "https://example.com"
        return httpx.Response(200, json=payload)

    http = httpx.Client(transport=_mock_transport({"/v1/scrape": handler}))
    result = scrape("https://example.com", client=http)
    assert result.title == "Hello World"
    assert result.markdown == "# Hello"


def test_firecrawl_scrape_raises_on_failure() -> None:
    http = httpx.Client(
        transport=_mock_transport(
            {
                "/v1/scrape": lambda _r: httpx.Response(500, text="fail"),
                "/v2/scrape": lambda _r: httpx.Response(500, text="fail"),
                "/scrape": lambda _r: httpx.Response(500, text="fail"),
            }
        )
    )
    with pytest.raises(FirecrawlError):
        scrape("https://example.com", client=http)


def _hunt_http_client() -> httpx.Client:
    searx_payload = {
        "results": [
            {
                "url": "https://nova.example",
                "title": "Nova Studio | Design",
                "content": "Boutique design studio",
            },
            {
                "url": "https://bad.example",
                "title": "Bad Site",
                "content": "will fail scrape",
            },
        ]
    }
    firecrawl_ok = {
        "data": {
            "markdown": "Nova Studio builds brand identities for startups.",
            "metadata": {"title": "Nova Studio"},
        }
    }

    def searx_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=searx_payload)

    def firecrawl_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        if body["url"] == "https://bad.example":
            return httpx.Response(500, text="scrape failed")
        return httpx.Response(200, json=firecrawl_ok)

    return httpx.Client(
        transport=_mock_transport(
            {
                "/search": searx_handler,
                "/v1/scrape": firecrawl_handler,
            }
        )
    )


def test_run_hunt_creates_leads_and_records_errors(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "hunt-run.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()
    init_db()

    http = _hunt_http_client()
    with patch("agent_crm.outbound_hunter.chat_completions") as mock_llm, patch(
        "agent_crm.outbound_hunter.assess_topical_relevance",
        return_value=_on_topic_assessment(),
    ):
        mock_llm.return_value = {
            "choices": [{"message": {"content": "Nova Studio is a boutique design shop."}}]
        }
        result = run_hunt(
            HuntRequest(
                query="boutique design studio",
                brand=Brand.MIDNIGHTSATIN,
                max_pages=5,
                search_limit=10,
            ),
            searx_client=http,
            firecrawl_client=http,
        )

    assert result.search_results == 2
    assert result.scraped == 1
    assert len(result.leads_created) == 1
    assert any("bad.example" in err for err in result.errors)

    crm = CRMToolkit(actor="api")
    lead = crm.get_lead(result.leads_created[0])
    assert lead.source == LeadSource.HUNTER
    assert lead.brand == Brand.MIDNIGHTSATIN
    assert lead.enrichment_summary == "Nova Studio is a boutique design shop."

    opp = crm.get_opportunity_for_lead(lead.id)
    assert opp.stage == Stage.PROSPECT

    heartbeats = {hb.agent_name: hb for hb in list_heartbeats()}
    assert "outbound_hunter" in heartbeats
    assert heartbeats["outbound_hunter"].status == AgentStatus.IDLE
    reset_engine()


def test_run_hunt_continues_when_searxng_fails(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "hunt-searx-fail.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()
    init_db()

    http = httpx.Client(
        transport=_mock_transport(
            {"/search": lambda _r: httpx.Response(503, text="down")}
        )
    )
    result = run_hunt(
        HuntRequest(query="anything"),
        searx_client=http,
        firecrawl_client=http,
    )
    assert result.leads_created == []
    assert result.search_results == 0
    assert result.errors
    reset_engine()


def test_hunt_api_endpoint(client: TestClient) -> None:
    from agent_crm.schemas import HuntResult

    with patch("agent_crm.api.run_hunt") as mock_run:
        mock_run.return_value = HuntResult(
            query="boutique studio NYC",
            brand=Brand.MIDNIGHTSATIN,
            search_results=1,
            scraped=1,
            leads_created=[1],
            errors=[],
        )
        response = client.post(
            "/hunt",
            json={
                "query": "boutique studio NYC",
                "brand": "midnightsatin",
                "max_pages": 3,
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["leads_created"] == [1]
    assert payload["scraped"] == 1
    assert payload["brand"] == "midnightsatin"


def test_run_hunt_skips_llm_when_disabled(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "hunt-no-llm.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()
    init_db()

    http = _hunt_http_client()
    with patch("agent_crm.outbound_hunter.chat_completions") as mock_llm:
        result = run_hunt(
            HuntRequest(query="studio", summarize=False, max_pages=1),
            searx_client=http,
            firecrawl_client=http,
        )
        mock_llm.assert_not_called()

    assert result.leads_created
    crm = CRMToolkit(actor="api")
    lead = crm.get_lead(result.leads_created[0])
    assert "Nova Studio" in (lead.enrichment_summary or "")
    reset_engine()
