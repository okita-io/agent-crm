"""Tests for the Research agent."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import AgentStatus, Brand, ResearchFindingKind
from agent_crm.heartbeat import list_heartbeats
from agent_crm.research import run_research
from agent_crm.research_store import list_findings
from agent_crm.research_utils import canonical_url, is_junk_finding
from agent_crm.schemas import ResearchFindingOut, ResearchRequest, ResearchResult


def _setup_db(tmp_path, monkeypatch, name: str) -> None:
    db_path = tmp_path / name
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()


def _teardown_db() -> None:
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch, "research-api.db")
    yield TestClient(app)
    _teardown_db()


def _mock_transport(handlers: dict[str, callable]) -> httpx.MockTransport:
    def _dispatch(request: httpx.Request) -> httpx.Response:
        for prefix, handler in handlers.items():
            if request.url.path.startswith(prefix) or str(request.url).startswith(prefix):
                return handler(request)
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(_dispatch)


def _research_http_client(
    *,
    searx_payload: dict | None = None,
    include_junk: bool = False,
) -> httpx.Client:
    results = [
        {
            "url": "https://co-star.app",
            "title": "Co–Star Astrology",
            "content": "Natal chart and daily horoscope app",
        },
        {
            "url": "https://junk.example/challenge",
            "title": "Just a moment...",
            "content": "Checking your browser before accessing",
        },
    ]
    if include_junk:
        results.append(
            {
                "url": "https://empty.example",
                "title": "",
                "content": "no title",
            }
        )
    payload = searx_payload or {"results": results}
    firecrawl_ok = {
        "data": {
            "markdown": "Co-Star offers personalized astrology based on NASA data.",
            "metadata": {"title": "Co-Star Astrology"},
        }
    }

    def searx_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    def firecrawl_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        if "junk.example" in body["url"] or "empty.example" in body["url"]:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "markdown": "Checking your browser",
                        "metadata": {"title": "Just a moment..."},
                    }
                },
            )
        return httpx.Response(200, json=firecrawl_ok)

    return httpx.Client(
        transport=_mock_transport(
            {
                "/search": searx_handler,
                "/v1/scrape": firecrawl_handler,
            }
        )
    )


def _nonprofit_http_client() -> httpx.Client:
    payload = {
        "results": [
            {
                "url": "https://friendship.org",
                "title": "Friendship Works | Elder Companionship",
                "content": "Nonprofit addressing loneliness among seniors EIN 12-3456789",
            },
        ]
    }
    firecrawl_ok = {
        "data": {
            "markdown": (
                "Friendship Works is a 501(c)(3) nonprofit. "
                "Our mission is reducing elder isolation. EIN 12-3456789."
            ),
            "metadata": {"title": "Friendship Works"},
        }
    }

    def searx_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    def firecrawl_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=firecrawl_ok)

    return httpx.Client(
        transport=_mock_transport(
            {
                "/search": searx_handler,
                "/v1/scrape": firecrawl_handler,
            }
        )
    )


def test_canonical_url_dedupes_tracking_params() -> None:
    a = canonical_url("https://Example.com/page?utm_source=x&id=1")
    b = canonical_url("https://example.com/page?id=1")
    assert a == b


def test_is_junk_finding_skips_cloudflare() -> None:
    assert is_junk_finding(title="Just a moment...", snippet="cloudflare")
    assert is_junk_finding(title="")
    assert not is_junk_finding(title="Co-Star Astrology", snippet="natal chart app")


def test_run_research_competitor_writes_celestial_nexus_findings(
    tmp_path, monkeypatch
) -> None:
    _setup_db(tmp_path, monkeypatch, "research-competitor.db")

    http = _research_http_client()
    with patch("agent_crm.research.chat_completions") as mock_llm:
        mock_llm.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "Co-Star is a leading natal chart app vs Celestial-Nexus.",
                                "why_it_matters": "Direct competitor in astrology apps.",
                            }
                        )
                    }
                }
            ]
        }
        result = run_research(
            ResearchRequest(
                brand=Brand.CELESTIAL_NEXUS,
                kind=ResearchFindingKind.COMPETITOR,
                query="natal chart app",
                max_pages=3,
                max_queries=1,
                write_accounts=False,
            ),
            searx_client=http,
            firecrawl_client=http,
        )

    assert result.pages_scraped == 1
    assert len(result.findings_written) == 1

    findings = list_findings(brand=Brand.CELESTIAL_NEXUS)
    assert len(findings) == 1
    assert findings[0].kind == ResearchFindingKind.COMPETITOR
    assert findings[0].domain == "co-star.app"
    assert "Celestial" in findings[0].summary or "Co-Star" in findings[0].summary

    heartbeats = {hb.agent_name: hb for hb in list_heartbeats()}
    assert heartbeats["research"].status == AgentStatus.IDLE
    _teardown_db()


def test_run_research_competitor_writes_midnightsatin_findings(
    tmp_path, monkeypatch
) -> None:
    _setup_db(tmp_path, monkeypatch, "research-ms.db")

    payload = {
        "results": [
            {
                "url": "https://galatea.com",
                "title": "Galatea — Immersive Fiction",
                "content": "Serialized romance reading app",
            }
        ]
    }
    http = _research_http_client(searx_payload=payload)
    with patch("agent_crm.research.chat_completions") as mock_llm:
        mock_llm.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "Galatea is a serial romance app vs MidnightSatin.",
                                "why_it_matters": "KU-class immersive fiction competitor.",
                            }
                        )
                    }
                }
            ]
        }
        result = run_research(
            ResearchRequest(
                brand=Brand.MIDNIGHTSATIN,
                query="serialized romance app",
                max_pages=2,
                max_queries=1,
                write_accounts=False,
            ),
            searx_client=http,
            firecrawl_client=http,
        )

    assert result.findings_written
    findings = list_findings(brand=Brand.MIDNIGHTSATIN, kind=ResearchFindingKind.COMPETITOR)
    assert findings[0].domain == "galatea.com"
    _teardown_db()


def test_run_research_nonprofit_writes_heybuddy_without_invented_ein(
    tmp_path, monkeypatch
) -> None:
    _setup_db(tmp_path, monkeypatch, "research-nonprofit.db")

    http = _nonprofit_http_client()
    with patch("agent_crm.research.chat_completions") as mock_llm:
        mock_llm.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "Friendship Works supports elder companionship.",
                                "org_name": "Friendship Works",
                                "mission": "Reducing elder isolation",
                                "ein": "99-9999999",
                                "why_it_matters": "Mission overlap with HeyBuddy.",
                            }
                        )
                    }
                }
            ]
        }
        result = run_research(
            ResearchRequest(
                brand=Brand.HEYBUDDY,
                query="501c3 elder companionship nonprofit",
                max_pages=2,
                max_queries=1,
                write_accounts=False,
            ),
            searx_client=http,
            firecrawl_client=http,
        )

    assert result.findings_written
    findings = list_findings(brand=Brand.HEYBUDDY, kind=ResearchFindingKind.NONPROFIT)
    assert findings[0].extra is not None
    assert findings[0].extra.get("ein") == "12-3456789"
    assert findings[0].extra.get("ein") != "99-9999999"
    _teardown_db()


def test_run_research_skips_junk_and_respects_page_budget(
    tmp_path, monkeypatch
) -> None:
    _setup_db(tmp_path, monkeypatch, "research-junk.db")

    http = _research_http_client(include_junk=True)
    with patch("agent_crm.research.chat_completions") as mock_llm:
        mock_llm.return_value = {
            "choices": [{"message": {"content": '{"summary": "ok"}'}}]
        }
        result = run_research(
            ResearchRequest(
                brand=Brand.CELESTIAL_NEXUS,
                query="natal chart app",
                max_pages=1,
                max_queries=1,
                summarize=False,
                write_accounts=False,
            ),
            searx_client=http,
            firecrawl_client=http,
        )

    assert result.pages_scraped == 1
    findings = list_findings(brand=Brand.CELESTIAL_NEXUS)
    assert len(findings) == 1
    assert findings[0].domain == "co-star.app"
    _teardown_db()


def test_run_research_stops_on_query_budget(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch, "research-budget.db")

    call_count = {"searches": 0}

    def searx_handler(request: httpx.Request) -> httpx.Response:
        page = dict(request.url.params).get("pageno", "1")
        if page != "1":
            return httpx.Response(200, json={"results": []})
        call_count["searches"] += 1
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": f"https://site{call_count['searches']}.example",
                        "title": f"Site {call_count['searches']}",
                        "content": "content",
                    }
                ]
            },
        )

    def firecrawl_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"data": {"markdown": "body", "metadata": {"title": body["url"]}}},
        )

    http = httpx.Client(
        transport=_mock_transport(
            {
                "/search": searx_handler,
                "/v1/scrape": firecrawl_handler,
            }
        )
    )

    with patch("agent_crm.research.chat_completions"):
        result = run_research(
            ResearchRequest(
                brand=Brand.CELESTIAL_NEXUS,
                max_queries=2,
                max_pages=2,
                summarize=False,
                write_accounts=False,
            ),
            searx_client=http,
            firecrawl_client=http,
        )

    assert result.queries_run == 2
    assert call_count["searches"] == 2
    _teardown_db()


def test_run_research_scrapes_beyond_legacy_four_page_run_cap(
    tmp_path, monkeypatch
) -> None:
    """Research runs must not stop after four total scraped pages."""
    _setup_db(tmp_path, monkeypatch, "research-run-cap.db")

    results = [
        {
            "url": f"https://org{idx}.example",
            "title": f"Org {idx}",
            "content": f"nonprofit mission {idx}",
        }
        for idx in range(6)
    ]

    def searx_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": results})

    scrape_calls = 0

    def firecrawl_handler(request: httpx.Request) -> httpx.Response:
        nonlocal scrape_calls
        scrape_calls += 1
        body = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "data": {
                    "markdown": f"Mission body for {body['url']}",
                    "metadata": {"title": body["url"]},
                }
            },
        )

    http = httpx.Client(
        transport=_mock_transport(
            {
                "/search": searx_handler,
                "/v1/scrape": firecrawl_handler,
            }
        )
    )

    with patch("agent_crm.research.chat_completions"):
        result = run_research(
            ResearchRequest(
                brand=Brand.HEYBUDDY,
                query="501c3 loneliness nonprofit",
                max_pages=6,
                max_queries=1,
                summarize=False,
                write_accounts=False,
            ),
            searx_client=http,
            firecrawl_client=http,
        )

    assert result.pages_scraped == 6
    assert scrape_calls == 6
    assert len(result.findings_written) == 6
    _teardown_db()


def test_research_api_endpoint(client: TestClient) -> None:
    with patch("agent_crm.api.run_research") as mock_run:
        mock_run.return_value = ResearchResult(
            brand=Brand.HEYBUDDY,
            kind=ResearchFindingKind.NONPROFIT,
            queries_run=1,
            pages_scraped=1,
            findings_written=[1],
            errors=[],
        )
        response = client.post(
            "/research",
            json={"brand": "heybuddy", "query": "501c3 loneliness nonprofit"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["brand"] == "heybuddy"
    assert payload["kind"] == "nonprofit"
    assert payload["findings_written"] == [1]


def test_research_findings_api(client: TestClient) -> None:
    with patch("agent_crm.api.list_findings") as mock_list:
        now = datetime.now(UTC)
        mock_list.return_value = [
            ResearchFindingOut(
                id=1,
                url="https://co-star.app",
                domain="co-star.app",
                title="Co-Star",
                brand=Brand.CELESTIAL_NEXUS,
                kind=ResearchFindingKind.COMPETITOR,
                summary="Competitor summary",
                source_query="natal chart app",
                raw_snippet="snippet",
                extra=None,
                first_seen_at=now,
                last_seen_at=now,
            )
        ]
        response = client.get("/research/findings?brand=celestial-nexus")
    assert response.status_code == 200
    assert response.json()[0]["domain"] == "co-star.app"
