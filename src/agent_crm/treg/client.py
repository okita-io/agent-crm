"""HTTP client for the treg catalog and /call/ proxy.

Auth is the org token from ``TREG_API_TOKEN`` / ``CRM_TREG_API_TOKEN``. The
proxy injects upstream credentials server-side; this process never holds them.
"""

from __future__ import annotations

import os
from typing import Any, Self

import httpx

from agent_crm.config import get_settings

DEFAULT_TREG_BASE_URL = "https://treg.to"


class TregError(Exception):
    """treg request failed."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def treg_api_token() -> str:
    """Return the configured treg token (env fallback for a bare ``TREG_API_TOKEN``)."""
    settings = get_settings()
    return (
        settings.treg_api_token
        or os.environ.get("TREG_API_TOKEN")
        or os.environ.get("TREG_TOKEN")
        or ""
    ).strip()


def treg_base_url() -> str:
    return (get_settings().treg_base_url or DEFAULT_TREG_BASE_URL).rstrip("/")


def treg_configured() -> bool:
    return bool(treg_api_token())


def _headers() -> dict[str, str]:
    token = treg_api_token()
    if not token:
        raise TregError("TREG_API_TOKEN is not set")
    headers = {"X-Treg-Token": token, "Accept": "application/json"}
    org = (get_settings().treg_org or "").strip()
    if org:
        headers["X-Treg-Org"] = org
    return headers


class TregClient:
    """Thin httpx wrapper around treg's catalog and call APIs."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        timeout: float = 45.0,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def catalog_search(self, query: str, *, limit: int = 50) -> dict[str, Any]:
        response = self._request(
            "GET",
            "/catalog/search",
            params={"q": query, "limit": max(1, min(limit, 100))},
        )
        payload = _json_payload(response)
        return payload if isinstance(payload, dict) else {"results": []}

    def catalog_get(self, endpoint_id: str) -> dict[str, Any]:
        response = self._request("GET", f"/catalog/endpoints/{endpoint_id}")
        payload = _json_payload(response)
        return payload if isinstance(payload, dict) else {}

    def balance(self) -> dict[str, Any]:
        org_id = self._resolve_org_id()
        response = self._request("GET", f"/orgs/{org_id}/balance", params={"limit": 5})
        payload = _json_payload(response)
        return payload if isinstance(payload, dict) else {}

    def _resolve_org_id(self) -> int:
        response = self._request("GET", "/orgs")
        payload = _json_payload(response)
        rows = payload if isinstance(payload, list) else []
        slug = (get_settings().treg_org or "").strip()
        chosen: dict[str, Any] | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if slug and row.get("slug") == slug:
                chosen = row
                break
            if chosen is None and row.get("active"):
                chosen = row
        if chosen is None and rows:
            first = rows[0]
            chosen = first if isinstance(first, dict) else None
        if chosen is None:
            raise TregError("could not resolve treg org id from GET /orgs")
        org_id = chosen.get("org_id") or chosen.get("id")
        if org_id is None:
            raise TregError("could not resolve treg org id from GET /orgs")
        return int(org_id)

    def call_endpoint(
        self,
        endpoint_id: str,
        *,
        method: str = "GET",
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        headers = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        verb = method.upper()
        if verb == "GET":
            response = self._request(
                "GET",
                f"/call/{endpoint_id}",
                params=query,
                extra_headers=headers,
            )
        else:
            response = self._request(
                verb,
                f"/call/{endpoint_id}",
                params=query,
                json_body=body,
                extra_headers=headers,
            )
        return _json_payload(response)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        headers = _headers()
        if extra_headers:
            headers.update(extra_headers)
        if json_body is not None:
            headers.setdefault("Content-Type", "application/json")
        url = f"{treg_base_url()}{path}"
        try:
            response = self._client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
            )
        except httpx.HTTPError as exc:
            raise TregError(f"treg {method} {path} failed: {exc}") from exc
        if response.status_code >= 400:
            detail = _error_detail(response)
            raise TregError(
                f"treg {method} {path} returned {response.status_code}: {detail}",
                status_code=response.status_code,
            )
        return response


def _json_payload(response: httpx.Response) -> Any:
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise TregError(f"treg returned non-JSON: {response.text[:300]}") from exc


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error") or payload
        return str(detail)[:500]
    return str(payload)[:500]
