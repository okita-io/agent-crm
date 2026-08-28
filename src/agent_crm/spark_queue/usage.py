"""Per-actor token accounting for Spark queue completions.

Prefers the OpenAI-compatible ``usage`` object returned by Spark/SGLang.
When the upstream omits it, tokens are estimated at ~4 characters each so the
Live Agents tab still has a credible avoided-cloud-cost story.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

CHARS_PER_TOKEN = 4
logger = logging.getLogger(__name__)


def chars_to_tokens(text: str) -> int:
    """Approximate token count using the common 4-characters-per-token heuristic."""
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def estimate_prompt_tokens(request_body: bytes) -> int:
    """Estimate prompt tokens from a chat/completions or completions request body."""
    if not request_body:
        return 0
    try:
        payload = json.loads(request_body)
    except json.JSONDecodeError:
        return chars_to_tokens(request_body.decode("utf-8", errors="ignore"))
    if not isinstance(payload, dict):
        return chars_to_tokens(request_body.decode("utf-8", errors="ignore"))

    parts: list[str] = []
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict):
                parts.append(_content_text(message.get("content")))
    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        parts.append(prompt)
    elif isinstance(prompt, list):
        parts.extend(item for item in prompt if isinstance(item, str))
    return chars_to_tokens("\n".join(part for part in parts if part))


def usage_from_payload(payload: dict) -> tuple[int, int] | None:
    """Return ``(prompt_tokens, completion_tokens)`` from an OpenAI-style payload."""
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    if prompt is None and completion is None:
        return None
    return int(prompt or 0), int(completion or 0)


def estimate_completion_tokens_from_json(payload: dict) -> int:
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return 0
    parts: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict):
            parts.append(_content_text(message.get("content")))
        text = choice.get("text")
        if isinstance(text, str):
            parts.append(text)
    return chars_to_tokens("\n".join(part for part in parts if part))


def _iter_sse_payloads(blob: bytes) -> list[dict]:
    text = blob.decode("utf-8", errors="ignore")
    payloads: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def usage_from_sse(blob: bytes) -> tuple[int, int] | None:
    """Return the last usage object found in an SSE stream, if any."""
    last: tuple[int, int] | None = None
    for payload in _iter_sse_payloads(blob):
        found = usage_from_payload(payload)
        if found is not None:
            last = found
    return last


def estimate_completion_tokens_from_sse(blob: bytes) -> int:
    parts: list[str] = []
    for payload in _iter_sse_payloads(blob):
        choices = payload.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict):
                parts.append(_content_text(delta.get("content")))
            message = choice.get("message")
            if isinstance(message, dict):
                parts.append(_content_text(message.get("content")))
            text = choice.get("text")
            if isinstance(text, str):
                parts.append(text)
    return chars_to_tokens("".join(part for part in parts if part))


def extract_exchange_tokens(
    request_body: bytes,
    response_body: bytes,
    *,
    streamed: bool,
) -> tuple[int, int, bool]:
    """Return ``(prompt, completion, estimated)`` for one proxy exchange."""
    if streamed:
        reported = usage_from_sse(response_body)
        if reported is not None:
            return reported[0], reported[1], False
        return (
            estimate_prompt_tokens(request_body),
            estimate_completion_tokens_from_sse(response_body),
            True,
        )

    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        reported = usage_from_payload(payload)
        if reported is not None:
            return reported[0], reported[1], False
        return (
            estimate_prompt_tokens(request_body),
            estimate_completion_tokens_from_json(payload),
            True,
        )
    return (
        estimate_prompt_tokens(request_body),
        chars_to_tokens(response_body.decode("utf-8", errors="ignore")),
        True,
    )


@dataclass
class ActorUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    requests: int = 0
    estimated_requests: int = 0
    first_seen_at: datetime | None = None

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "requests": self.requests,
            "estimated_requests": self.estimated_requests,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
        }


PersistFn = Callable[[str, int, int, bool], None]


@dataclass
class TokenUsageLedger:
    """Thread-safe in-memory token totals keyed by ``X-CRM-Agent`` actor."""

    persist: PersistFn | None = None
    _by_actor: dict[str, ActorUsage] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(
        self,
        actor: str,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        estimated: bool = False,
    ) -> None:
        prompt = max(0, int(prompt_tokens))
        completion = max(0, int(completion_tokens))
        if prompt == 0 and completion == 0:
            return
        label = (actor or "external").strip() or "external"
        seen = datetime.now(UTC)
        with self._lock:
            row = self._by_actor.setdefault(label, ActorUsage())
            if row.first_seen_at is None:
                row.first_seen_at = seen
            row.prompt_tokens += prompt
            row.completion_tokens += completion
            row.requests += 1
            if estimated:
                row.estimated_requests += 1
        if self.persist is not None:
            try:
                self.persist(label, prompt, completion, estimated)
            except Exception:
                logger.warning("token usage persist callback failed", exc_info=True)

    def record_exchange(
        self,
        actor: str,
        request_body: bytes,
        response_body: bytes,
        *,
        streamed: bool = False,
    ) -> None:
        prompt, completion, estimated = extract_exchange_tokens(
            request_body,
            response_body,
            streamed=streamed,
        )
        self.record(actor, prompt, completion, estimated=estimated)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            by_actor = {
                actor: usage.as_dict()
                for actor, usage in sorted(self._by_actor.items())
            }
        totals = {
            "prompt_tokens": sum(int(row["prompt_tokens"] or 0) for row in by_actor.values()),
            "completion_tokens": sum(
                int(row["completion_tokens"] or 0) for row in by_actor.values()
            ),
            "requests": sum(int(row["requests"] or 0) for row in by_actor.values()),
            "estimated_requests": sum(
                int(row["estimated_requests"] or 0) for row in by_actor.values()
            ),
        }
        first_seen = None
        for row in by_actor.values():
            stamp = row.get("first_seen_at")
            if isinstance(stamp, str) and (first_seen is None or stamp < first_seen):
                first_seen = stamp
        totals["first_seen_at"] = first_seen
        return {"by_actor": by_actor, "totals": totals}
