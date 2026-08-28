"""OpenAI-compatible Spark SGLang proxy with global occupancy-aware queuing."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from agent_crm.presence import AGENT_IDENTITY_HEADER

from .config import get_spark_queue_settings
from .gate import GlobalConcurrencyGate, QueueTimeoutError
from .occupancy import SparkOccupancyBackend, SparkOccupancyClient
from .usage import TokenUsageLedger

logger = logging.getLogger(__name__)
settings = get_spark_queue_settings()
occupancy_client = SparkOccupancyClient(
    SparkOccupancyBackend(settings.origin_url)
)
gate = GlobalConcurrencyGate(
    occupancy_client=occupancy_client,
    max_concurrency=settings.max_concurrency,
    queue_timeout=settings.queue_timeout,
    poll_interval=settings.occupancy_poll_interval,
)
http_client: httpx.AsyncClient | None = None


def _persist_token_usage(
    actor: str, prompt_tokens: int, completion_tokens: int, estimated: bool
) -> None:
    """Write one completion's tokens to the CRM store. Failures must not block GPU work."""
    try:
        from agent_crm.token_usage_store import record_token_usage

        record_token_usage(
            actor,
            prompt_tokens,
            completion_tokens,
            estimated=estimated,
        )
    except Exception as exc:  # noqa: BLE001 - never fail a Spark completion
        logger.warning("token usage persist failed: %s", exc)


usage_ledger = TokenUsageLedger(persist=_persist_token_usage)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global http_client
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(None, connect=30.0),
        follow_redirects=True,
    )
    yield
    await http_client.aclose()
    http_client = None


app = FastAPI(
    title="Spark SGLang Queue",
    description=(
        "OpenAI-compatible proxy that enforces a global Spark session cap "
        "across Hermes and CRM agents."
    ),
    lifespan=lifespan,
)


def _client() -> httpx.AsyncClient:
    if http_client is None:
        raise RuntimeError("Spark queue HTTP client is not initialized")
    return http_client


def _upstream_url(path: str) -> str:
    return f"{settings.base_url.rstrip('/')}/{path.lstrip('/')}"


def _request_actor(request: Request) -> str:
    return request.headers.get(AGENT_IDENTITY_HEADER, "external")


def _serialize_actor_entries(entries: list) -> list[dict[str, str | float]]:
    now = time.monotonic()
    return [
        {
            "actor": entry.actor,
            "since_seconds": round(now - entry.since, 2),
        }
        for entry in entries
    ]


async def _proxy_request(request: Request, upstream_path: str) -> Response:
    body = await request.body()
    actor = _request_actor(request)
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length", "connection"}
    }
    is_stream = False
    if body:
        try:
            payload = json.loads(body)
            is_stream = bool(payload.get("stream"))
        except json.JSONDecodeError:
            pass

    try:
        await gate.acquire(actor)
    except QueueTimeoutError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "message": str(exc),
                    "type": "queue_timeout",
                    "code": "spark_queue_timeout",
                }
            },
        )

    if is_stream:
        return StreamingResponse(
            _stream_upstream(request.method, upstream_path, body, headers, actor),
            media_type="text/event-stream",
            status_code=200,
        )

    try:
        upstream = await _client().request(
            request.method,
            _upstream_url(upstream_path),
            content=body,
            headers=headers,
        )
        if 200 <= upstream.status_code < 300:
            usage_ledger.record_exchange(actor, body, upstream.content, streamed=False)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=dict(upstream.headers),
            media_type=upstream.headers.get("content-type"),
        )
    finally:
        await gate.release(actor)


async def _stream_upstream(
    method: str,
    upstream_path: str,
    body: bytes,
    headers: dict[str, str],
    actor: str,
) -> AsyncIterator[bytes]:
    collected = bytearray()
    status_code = 0
    try:
        async with _client().stream(
            method,
            _upstream_url(upstream_path),
            content=body,
            headers=headers,
        ) as upstream:
            status_code = upstream.status_code
            async for chunk in upstream.aiter_bytes():
                collected.extend(chunk)
                yield chunk
    finally:
        if 200 <= status_code < 300:
            usage_ledger.record_exchange(actor, body, bytes(collected), streamed=True)
        await gate.release(actor)


@app.get("/health", tags=["system"])
async def health() -> dict:
    await occupancy_client.observe_running_count()
    observed = occupancy_client.last_observed
    local = gate.local_in_flight
    return {
        "status": "ok",
        "local_in_flight": local,
        "waiting": gate.waiting,
        "observed_upstream_in_flight": observed,
        "max_concurrency": gate.max_concurrency,
        "upstream": settings.base_url,
        "model": settings.model,
        "waiters": _serialize_actor_entries(gate.waiters),
        "in_flight": _serialize_actor_entries(gate.in_flight),
        "external_upstream_slots": max(0, (observed or 0) - local),
        "token_usage": usage_ledger.snapshot(),
    }


@app.get("/v1/models", tags=["openai"])
async def list_models() -> Response:
    upstream = await _client().get(_upstream_url("models"))
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


@app.post("/v1/chat/completions", tags=["openai"])
async def chat_completions(request: Request) -> Response:
    return await _proxy_request(request, "chat/completions")


@app.post("/v1/completions", tags=["openai"])
async def completions(request: Request) -> Response:
    return await _proxy_request(request, "completions")
