"""Turn a treg /call/ response into SearXNG-shaped search hits."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from agent_crm.searxng_client import SearchResult
from .client import TregClient, TregError
from .store import get_treg_tool, treg_tool_allowed

_SEARX_PARAM_KEYS = frozenset(
    {"categories", "pageno", "time_range", "language", "engines"}
)
_TREG_PARAM_KEYS = frozenset(
    {
        "treg_endpoint_id",
        "treg_paid",
        "treg_query_param",
        "treg_method",
    }
)

_URL_KEYS = (
    "url",
    "link",
    "profile_url",
    "linkedin_url",
    "website",
    "permalink",
    "share_url",
    "canonical_url",
)
_TITLE_KEYS = (
    "title",
    "name",
    "full_name",
    "username",
    "uniqueId",
    "handle",
    "display_name",
)
_SNIPPET_KEYS = (
    "snippet",
    "description",
    "content",
    "bio",
    "text",
    "email",
    "headline",
    "summary",
)
_SEARCH_PARAM_NAMES = (
    "q",
    "query",
    "keyword",
    "keywords",
    "search",
    "term",
    "text",
    "uniqueId",
    "username",
    "handle",
    "domain",
    "url",
    "full_name",
    "name",
)


def _first_str(node: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def treg_endpoint_from(origin: str | None, params: dict[str, Any] | None) -> str | None:
    if isinstance(params, dict):
        raw = params.get("treg_endpoint_id")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    value = (origin or "").strip()
    if not value.startswith("treg:"):
        return None
    parts = value.split(":", 3)
    if len(parts) == 4:
        return parts[3] or None
    if len(parts) >= 2:
        return parts[-1] or None
    return None


def treg_origin(endpoint_id: str, *, paid: bool, queue_as: str) -> str:
    bucket = "paid" if paid else "free"
    role = queue_as if queue_as in {"hunter", "research"} else "hunter"
    return f"treg:{bucket}:{role}:{endpoint_id}"[:128]


def searx_kwargs_from_params(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    return {key: value for key, value in params.items() if key in _SEARX_PARAM_KEYS}


def extract_search_hits(payload: Any, *, limit: int = 50) -> list[SearchResult]:
    """Walk an arbitrary treg JSON body for URLs, titles, and snippets."""
    collected: list[SearchResult] = []
    seen: set[str] = set()

    def add(url: str, title: str, snippet: str) -> None:
        cleaned = url.strip()
        if not cleaned or cleaned in seen:
            return
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return
        seen.add(cleaned)
        collected.append(
            SearchResult(
                url=cleaned,
                title=(title or parsed.netloc)[:512],
                snippet=(snippet or "")[:1000],
            )
        )

    def walk(node: Any) -> None:
        if len(collected) >= limit:
            return
        if isinstance(node, dict):
            url = _first_str(node, _URL_KEYS)
            if url:
                add(url, _first_str(node, _TITLE_KEYS), _first_str(node, _SNIPPET_KEYS))
            for value in node.values():
                walk(value)
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if isinstance(node, str) and node.startswith("http"):
            add(node, "", "")

    walk(payload)
    return collected[:limit]


def build_treg_request(
    *,
    method: str,
    input_schema: dict[str, Any] | None,
    query: str,
    extra: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """Map a hunt/research query onto the endpoint's documented params."""
    extra = {key: value for key, value in (extra or {}).items() if key not in _TREG_PARAM_KEYS}
    schema = input_schema or {}
    query_params_spec = schema.get("queryParams") if isinstance(schema.get("queryParams"), dict) else {}
    body_spec = schema.get("body") if isinstance(schema.get("body"), dict) else {}
    query_params: dict[str, Any] = {}
    body: dict[str, Any] = {}

    def fill(spec: dict[str, Any], target: dict[str, Any]) -> None:
        for name, meta in spec.items():
            if name in extra and extra[name] not in (None, ""):
                target[name] = extra[name]
                continue
            required = isinstance(meta, dict) and bool(meta.get("required"))
            if query and (required or name in _SEARCH_PARAM_NAMES):
                target[name] = query
                return

    if query_params_spec:
        fill(query_params_spec, query_params)
    if body_spec:
        fill(body_spec, body)
    if not query_params and not body and query:
        if method.upper() == "GET":
            query_params["q"] = query
        else:
            body["query"] = query
    return method.upper(), query_params or None, body or None


def search_treg(
    endpoint_id: str,
    query: str,
    *,
    limit: int = 50,
    extra: dict[str, Any] | None = None,
    client: TregClient | None = None,
) -> list[SearchResult]:
    """Call one catalog endpoint and coerce the body into search hits."""
    if not treg_tool_allowed(endpoint_id):
        raise TregError(
            f"treg endpoint {endpoint_id} is paid and not allowlisted"
        )
    tool = get_treg_tool(endpoint_id)
    method = (tool.method if tool is not None else "GET") or "GET"
    input_schema = tool.input_schema if tool is not None else None
    call_method, query_params, body = build_treg_request(
        method=method,
        input_schema=input_schema,
        query=query,
        extra=extra,
    )
    owns = client is None
    http = client or TregClient()
    try:
        payload = http.call_endpoint(
            endpoint_id,
            method=call_method,
            query=query_params,
            body=body,
        )
    finally:
        if owns:
            http.close()
    return extract_search_hits(payload, limit=limit)


def collect_search_results(
    query: str,
    *,
    limit: int,
    origin: str = "",
    params: dict[str, Any] | None = None,
    searx_client: Any = None,
) -> list[SearchResult]:
    """SearXNG by default; treg when the queued row names an endpoint."""
    from agent_crm.searxng_client import search

    endpoint_id = treg_endpoint_from(origin, params)
    if endpoint_id:
        extra = {
            key: value
            for key, value in (params or {}).items()
            if key not in _TREG_PARAM_KEYS and key not in _SEARX_PARAM_KEYS
        }
        return search_treg(endpoint_id, query, limit=limit, extra=extra)
    return search(
        query,
        limit=limit,
        client=searx_client,
        **searx_kwargs_from_params(params),
    )
