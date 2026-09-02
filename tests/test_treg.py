"""treg catalog classification, allowlist, and hunt/research queueing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import Brand, HuntQueryStatus, ResearchFindingKind, ResearchQueryStatus
from agent_crm.hunt.store import HuntStore
from agent_crm.research.query_store import ResearchQueryStore
from agent_crm.treg.catalog import (
    catalog_tool_from_row,
    classify_queue_as,
    collect_catalog_tools,
    is_free_cost,
)
from agent_crm.treg.client import TregError
from agent_crm.treg.queue import allow_treg_tools, endpoint_accepts_search_query
from agent_crm.treg.search import (
    build_treg_request,
    collect_search_results,
    extract_search_hits,
    search_treg,
    treg_endpoint_from,
    treg_origin,
)
from agent_crm.treg.store import (
    get_treg_tool,
    list_treg_tools,
    sync_treg_catalog,
    upsert_catalog_tools,
)


@pytest.fixture()
def treg_db(tmp_path, monkeypatch):
    db_path = tmp_path / "treg.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("CRM_API_TOKEN", "")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield db_path
    reset_engine()
    get_settings.cache_clear()


def test_is_free_cost_and_queue_classification() -> None:
    assert is_free_cost({"type": "free", "usd": 0})
    assert is_free_cost({"type": "per_call", "usd": 0.0})
    assert not is_free_cost({"type": "per_success", "usd": 0.0049})
    assert classify_queue_as({"kind": "routed", "capability": "people.email.find", "platform": "people"}) == "hunter"
    assert classify_queue_as({"kind": "data", "capability": "google.serp.organic", "platform": "google"}) == "research"
    assert classify_queue_as({"kind": "utility", "capability": "serp.locations", "platform": "google"}) == "skip"


def test_collect_catalog_hides_children_of_routed_jobs() -> None:
    payload = {
        "results": [
            {
                "id": "treg.people.email.find",
                "name": "Find work email",
                "summary": "Routed email finder",
                "provider": "treg",
                "capability": "people.email.find",
                "platform": "people",
                "method": "POST",
                "path": "/people.email.find",
                "kind": "routed",
                "cost": {"type": "per_success", "usd": 0.0089},
                "input": {"body": {"full_name": {"type": "str"}, "domain": {"type": "str"}}},
            },
            {
                "id": "tomba.people.email.find",
                "name": "Tomba email",
                "summary": "Child provider",
                "provider": "tomba",
                "capability": "people.email.find",
                "platform": "people",
                "method": "GET",
                "path": "/v1/email-finder",
                "kind": "data",
                "cost": {"type": "per_success", "usd": 0.0089},
            },
            {
                "id": "akta.companies.industry.resolve",
                "name": "Industry resolve",
                "summary": "Free utility",
                "provider": "akta",
                "capability": "companies.industry.resolve",
                "platform": "companies",
                "method": "GET",
                "kind": "utility",
                "cost": {"type": "free", "usd": 0},
                "input": {"queryParams": {"query": {"type": "string", "required": True}}},
            },
        ]
    }
    tools = collect_catalog_tools([("hunter", payload)])
    by_id = {tool.endpoint_id: tool for tool in tools}
    assert by_id["treg.people.email.find"].selectable is True
    assert by_id["treg.people.email.find"].queue_as == "hunter"
    assert by_id["treg.people.email.find"].is_free is False
    assert by_id["tomba.people.email.find"].selectable is False
    assert by_id["akta.companies.industry.resolve"].is_free is True


def test_extract_hits_and_request_mapping() -> None:
    hits = extract_search_hits(
        {
            "output": {"full_name": "Ada Lovelace", "linkedin_url": "https://www.linkedin.com/in/ada"},
            "results": [
                {"title": "Forum", "url": "https://example.com/forum", "snippet": "readers"},
            ],
        }
    )
    urls = {hit.url for hit in hits}
    assert "https://www.linkedin.com/in/ada" in urls
    assert "https://example.com/forum" in urls
    method, query, body = build_treg_request(
        method="POST",
        input_schema={"body": {"keyword": {"type": "str", "required": True}}},
        query="romance booktok",
    )
    assert method == "POST"
    assert body == {"keyword": "romance booktok"}
    assert query is None
    assert endpoint_accepts_search_query({"queryParams": {"q": {"required": True}}})


def test_treg_origin_roundtrip() -> None:
    origin = treg_origin("treg.google.serp.organic", paid=True, queue_as="research")
    assert origin.startswith("treg:paid:research:")
    assert treg_endpoint_from(origin, None) == "treg.google.serp.organic"
    assert (
        treg_endpoint_from("seed", {"treg_endpoint_id": "treg.people.enrich"})
        == "treg.people.enrich"
    )


def test_sync_upserts_and_auto_allows_free(treg_db) -> None:
    client = MagicMock()
    client.catalog_search.return_value = {
        "results": [
            {
                "id": "treg.google.serp.organic",
                "name": "Google organic",
                "summary": "SERP links",
                "provider": "treg",
                "capability": "google.serp.organic",
                "platform": "google",
                "method": "GET",
                "kind": "routed",
                "cost": {"type": "per_success", "usd": 0.00188},
                "input": {"queryParams": {"q": {"type": "str", "required": True}}},
            },
            {
                "id": "tomba.web.domain.disposable",
                "name": "Disposable domain",
                "summary": "Free check",
                "provider": "tomba",
                "capability": "web.domain.disposable",
                "platform": "web",
                "method": "GET",
                "kind": "data",
                "cost": {"type": "free", "usd": 0},
                "input": {"queryParams": {"domain": {"type": "str", "required": True}}},
            },
        ]
    }
    result = sync_treg_catalog(client=client)
    assert result.upserted >= 2
    serp = get_treg_tool("treg.google.serp.organic")
    free = get_treg_tool("tomba.web.domain.disposable")
    assert serp is not None and serp.allowed is False and serp.is_free is False
    assert free is not None and free.allowed is True and free.is_free is True


def test_allow_paid_serp_queues_research(treg_db) -> None:
    tool = catalog_tool_from_row(
        {
            "id": "treg.google.serp.organic",
            "name": "Google organic",
            "summary": "SERP links",
            "provider": "treg",
            "capability": "google.serp.organic",
            "platform": "google",
            "method": "GET",
            "kind": "routed",
            "cost": {"type": "per_success", "usd": 0.00188},
            "input": {"queryParams": {"q": {"type": "str", "required": True}}},
        },
        hint="research",
    )
    assert tool is not None
    upsert_catalog_tools([tool])
    result = allow_treg_tools(["treg.google.serp.organic"])
    assert "treg.google.serp.organic" in result.allowed
    assert result.research_enqueued >= 1
    row = get_treg_tool("treg.google.serp.organic")
    assert row is not None and row.allowed is True
    store = ResearchQueryStore()
    claimed = store.claim_next_pending_query(kind=ResearchFindingKind.COMPETITOR)
    if claimed is None:
        claimed = store.claim_next_pending_query()
    assert claimed is not None
    assert claimed.origin.startswith("treg:paid:research:")
    assert claimed.status == ResearchQueryStatus.RUNNING


def test_allow_paid_people_queues_hunter(treg_db) -> None:
    from agent_crm.db import session_scope
    from agent_crm.models import ContactProfile

    with session_scope() as session:
        session.add(
            ContactProfile(
                email="ada@example.com",
                name="Ada Lovelace",
                brand=Brand.TACTIC_STUDIO,
            )
        )
    tool = catalog_tool_from_row(
        {
            "id": "treg.people.email.find",
            "name": "Find work email",
            "summary": "People follow-up",
            "provider": "treg",
            "capability": "people.email.find",
            "platform": "people",
            "method": "POST",
            "kind": "routed",
            "cost": {"type": "per_success", "usd": 0.0089},
            "input": {"body": {"full_name": {"type": "str"}, "domain": {"type": "str"}}},
        },
        hint="hunter",
    )
    assert tool is not None
    upsert_catalog_tools([tool])
    result = allow_treg_tools(["treg.people.email.find"])
    assert result.hunt_enqueued >= 1
    pending = HuntStore().claim_next_pending_query(brand=Brand.TACTIC_STUDIO)
    assert pending is not None
    assert pending.status == HuntQueryStatus.RUNNING
    assert pending.params is not None
    assert pending.params["treg_endpoint_id"] == "treg.people.email.find"
    assert pending.origin.startswith("treg:paid:hunter:")


def test_paid_treg_call_blocked_until_allowed(treg_db) -> None:
    tool = catalog_tool_from_row(
        {
            "id": "treg.google.serp.organic",
            "name": "Google organic",
            "summary": "SERP",
            "provider": "treg",
            "capability": "google.serp.organic",
            "platform": "google",
            "method": "GET",
            "kind": "routed",
            "cost": {"type": "per_success", "usd": 0.002},
            "input": {"queryParams": {"q": {"required": True}}},
        },
        hint="research",
    )
    assert tool is not None
    upsert_catalog_tools([tool])
    with pytest.raises(TregError, match="not allowlisted"):
        search_treg("treg.google.serp.organic", "romance forums")


def test_collect_search_results_uses_treg_when_queued(treg_db, monkeypatch) -> None:
    tool = catalog_tool_from_row(
        {
            "id": "treg.google.serp.organic",
            "name": "Google organic",
            "summary": "SERP",
            "provider": "treg",
            "capability": "google.serp.organic",
            "platform": "google",
            "method": "GET",
            "kind": "routed",
            "cost": {"type": "per_success", "usd": 0.002},
            "input": {"queryParams": {"q": {"required": True}}},
        },
        hint="research",
    )
    assert tool is not None
    upsert_catalog_tools([tool])
    allow_treg_tools(["treg.google.serp.organic"])

    def fake_search_treg(endpoint_id, query, *, limit=50, extra=None, client=None):
        assert endpoint_id == "treg.google.serp.organic"
        from agent_crm.searxng_client import SearchResult

        return [SearchResult(url="https://example.com/hit", title=query, snippet="ok")]

    monkeypatch.setattr("agent_crm.treg.search.search_treg", fake_search_treg)
    hits = collect_search_results(
        "natal chart app",
        limit=10,
        origin=treg_origin("treg.google.serp.organic", paid=True, queue_as="research"),
        params={"treg_endpoint_id": "treg.google.serp.organic", "categories": "news"},
    )
    assert hits[0].url == "https://example.com/hit"


def test_list_paid_selectable_excludes_free(treg_db) -> None:
    payload = {
        "results": [
            {
                "id": "treg.people.enrich",
                "name": "Enrich person",
                "summary": "Paid people",
                "provider": "treg",
                "capability": "people.enrich",
                "platform": "people",
                "method": "POST",
                "kind": "routed",
                "cost": {"type": "per_success", "usd": 0.0049},
            },
            {
                "id": "dataforseo.x.serp-yahoo-locations-country",
                "name": "Yahoo locations",
                "summary": "Free locations",
                "provider": "dataforseo",
                "capability": "serp.yahoo.locations",
                "platform": "yahoo",
                "method": "GET",
                "kind": "utility",
                "cost": {"type": "free", "usd": 0},
            },
        ]
    }
    client = MagicMock()
    client.catalog_search.return_value = payload
    sync_treg_catalog(client=client)
    paid = list_treg_tools(paid=True, selectable=True)
    assert any(row.endpoint_id == "treg.people.enrich" for row in paid)
    assert all(not row.is_free for row in paid)
