"""Spark / OpenAI-compatible LLM client for hunter extraction."""

from __future__ import annotations

import json
import re

import httpx

from agent_crm.config import Settings, get_settings


class LlmClient:
    """Call the ranch LLM (spark-queue) for structured extraction."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return self.settings.hunter_enable_llm and bool(self.settings.llm_base_url)

    def extract_follow_up_terms(
        self,
        *,
        query: str,
        results: list[dict],
        max_terms: int,
    ) -> list[str]:
        """Ask the LLM for follow-up search terms (once per query, not per page)."""
        if not self.enabled or not results:
            return []

        lines = []
        for idx, item in enumerate(results[:12], start=1):
            lines.append(
                f"{idx}. {item.get('title', '')} | {item.get('url', '')} | "
                f"{(item.get('content') or '')[:200]}"
            )
        prompt = (
            "You help an outbound researcher find online communities, directories, "
            "newsletters, forums, and listicles where potential users gather.\n"
            f"Original query: {query}\n"
            "Search results:\n"
            + "\n".join(lines)
            + "\n\n"
            f"Suggest up to {max_terms} NEW search queries to find more such resources. "
            "Focus on communities, directories, newsletters, forums — not individual people. "
            "Do NOT invent emails or person names.\n"
            'Respond with JSON only: {"terms": ["query one", "query two"]}'
        )

        try:
            with httpx.Client(timeout=self.settings.hunter_request_timeout) as client:
                response = client.post(
                    f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                    json={
                        "model": self.settings.llm_model,
                        "messages": [
                            {"role": "system", "content": "You output JSON only."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.2,
                    },
                    headers={"X-Actor": "outbound_hunter"},
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            return []

        return _parse_terms_json(content, max_terms)


def _parse_terms_json(content: str, max_terms: int) -> list[str]:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    terms = payload.get("terms") or payload.get("queries") or []
    cleaned: list[str] = []
    for term in terms:
        if isinstance(term, str) and term.strip():
            cleaned.append(term.strip())
        if len(cleaned) >= max_terms:
            break
    return cleaned
