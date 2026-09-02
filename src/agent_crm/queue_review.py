"""Queue-review agent: keep or toss hunter-added search terms before they run.

Seed packs and named venues go straight to PENDING. Hunter-added branch,
community, person, handle, and follow-up terms land in PENDING_REVIEW. This
loop drains that backlog with a cheap deterministic gate, then Spark only
for ambiguous terms.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .agent_control import stop_if_disabled, wait_while_disabled
from .config import get_settings
from agent_crm.engagement.query_store import EngagementQueryStore
from .enums import AgentStatus, Brand, ImprovementSourceAgent
from .heartbeat import record_heartbeat
from agent_crm.hunt.relevance import BRAND_ON_TOPIC_KEYWORDS, BRAND_TOPIC_SUMMARIES
from agent_crm.hunt.store import HuntStore
from agent_crm.hunt.utils import (
    ASTROLOGY_OUTLET_HOSTS,
    ROMANCE_OUTLET_HOSTS,
    normalize_query,
    origin_needs_review,
)
from .llm_client import chat_completions
from .llm_text import UNTRUSTED_DATA_SYSTEM_SUFFIX, extract_json_object, wrap_untrusted
from agent_crm.research.query_store import ResearchQueryStore

logger = logging.getLogger(__name__)

ACTOR = "queue-review"
WATCH_POLL_SECONDS = 20.0

_NOISE_QUERY_FRAGMENTS: tuple[str, ...] = (
    "mozilla",
    "developer.mozilla",
    "mdn web",
    "docker hub",
    "hub.docker",
    "haskell.org",
    "hackage",
    "stackoverflow",
    "stackexchange",
    "npmjs",
    "pypi.org",
    "wikipedia",
    "w3.org",
    "reuters.com",
    "flipboard",
    "merriam-webster",
    "dictionary.com",
    "britannica",
    "kubernetes",
    "dockerfile",
)

_GENERIC_DISCOVERY_TOKENS: tuple[str, ...] = (
    "forum",
    "community",
    "newsletter",
    "discord",
    "reddit",
    "subreddit",
    "blog",
    "podcast",
)

_VERTICAL_HOST_HINTS: dict[Brand, frozenset[str]] = {
    Brand.MIDNIGHTSATIN: ROMANCE_OUTLET_HOSTS | frozenset(
        {"r/romance", "booktok", "bookstagram", "wattpad", "kirkus", "bookriot"}
    ),
    Brand.CELESTIAL_NEXUS: ASTROLOGY_OUTLET_HOSTS | frozenset(
        {"r/astrology", "horoscope", "natal chart", "tarot", "witchtok"}
    ),
    Brand.HEYBUDDY: frozenset(
        {"loneliness", "veteran", "companion", "wellness", "caregiver"}
    ),
    Brand.TACTIC_STUDIO: frozenset(
        {"webar", "webxr", "augmented reality", "vp of marketing", "brand manager"}
    ),
}


@dataclass(frozen=True)
class QueueReviewDecision:
    keep: bool
    reason: str
    spark_used: bool = False


@dataclass
class QueueReviewBudget:
    max_queries: int = 40
    max_minutes: int = 10
    allow_spark: bool = True
    spark_per_cycle: int = 8


@dataclass
class QueueReviewResult:
    reviewed: int = 0
    kept: int = 0
    tossed: int = 0
    spark_used: int = 0
    stop_reason: str = "queue_empty"
    errors: list[str] = field(default_factory=list)


def assess_search_query(
    *,
    brand: Brand,
    query: str,
    origin: str | None = None,
    allow_spark: bool = True,
) -> QueueReviewDecision:
    """Decide whether a queued search term is worth running."""
    cleaned = normalize_query(query)
    if len(cleaned) < 8:
        return QueueReviewDecision(keep=False, reason="query too short")

    if any(fragment in cleaned for fragment in _NOISE_QUERY_FRAGMENTS):
        return QueueReviewDecision(keep=False, reason="noise host or docs token in query")

    if not origin_needs_review(origin):
        return QueueReviewDecision(keep=True, reason="trusted seed origin")

    keywords = BRAND_ON_TOPIC_KEYWORDS.get(brand, ())
    keyword_hits = sum(1 for keyword in keywords if keyword in cleaned)
    hints = _VERTICAL_HOST_HINTS.get(brand, frozenset())
    hint_hits = sum(1 for hint in hints if hint in cleaned)

    if keyword_hits >= 1 or hint_hits >= 1:
        return QueueReviewDecision(
            keep=True,
            reason=f"on-brand search term ({keyword_hits} keywords, {hint_hits} vertical hints)",
        )

    generic = any(token in cleaned for token in _GENERIC_DISCOVERY_TOKENS)
    if generic and allow_spark:
        spark = _spark_query_assessment(brand=brand, query=query, origin=origin)
        if spark is not None:
            return spark

    return QueueReviewDecision(
        keep=False,
        reason="no vertical signal; toss hunter-added term",
    )


def _spark_query_assessment(
    *,
    brand: Brand,
    query: str,
    origin: str | None,
) -> QueueReviewDecision | None:
    topic = BRAND_TOPIC_SUMMARIES.get(brand, brand.value)
    prompt = (
        "Decide if this search query is worth running for a CRM hunter that "
        "collects outreach targets (outlets, communities, creators) — not docs, "
        "dev tooling, aggregators, or random profiles.\n"
        f"Brand topic: {topic}\n"
        f"Origin: {origin or 'n/a'}\n"
        f"{wrap_untrusted('query', query, max_chars=400)}\n"
        "Return JSON only: "
        '{"keep":true|false,"reason":"short reason"}.'
    )
    try:
        response = chat_completions(
            {
                "model": "crm",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You review CRM search-queue terms. Output JSON only."
                            + UNTRUSTED_DATA_SYSTEM_SUFFIX
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 160,
            },
            timeout=60.0,
            actor=ACTOR,
            task=f"queue review {brand.value}",
        )
        content = response["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        logger.exception("Spark queue review failed for %r", query)
        return None

    payload = extract_json_object(content)
    if not payload:
        return None
    keep = bool(payload.get("keep"))
    reason = str(payload.get("reason") or "spark queue review").strip()[:500]
    return QueueReviewDecision(keep=keep, reason=reason, spark_used=True)


def run_queue_review(
    *,
    budget: QueueReviewBudget | None = None,
) -> QueueReviewResult:
    """Review pending_review rows on hunt, research, and engagement queues."""
    budget = budget or QueueReviewBudget()
    result = QueueReviewResult()
    if stop_if_disabled(ACTOR):
        result.stop_reason = "disabled"
        return result
    deadline = None
    if budget.max_minutes > 0:
        deadline = time.monotonic() + budget.max_minutes * 60
    spark_left = budget.spark_per_cycle if budget.allow_spark else 0

    hunt = HuntStore()
    research = ResearchQueryStore()
    engagement = EngagementQueryStore()

    record_heartbeat(ACTOR, status=AgentStatus.THINKING, task="reviewing search queues")

    while result.reviewed < budget.max_queries or budget.max_queries <= 0:
        if stop_if_disabled(ACTOR):
            result.stop_reason = "disabled"
            break
        if deadline is not None and time.monotonic() >= deadline:
            result.stop_reason = "max_minutes"
            break
        if budget.max_queries > 0 and result.reviewed >= budget.max_queries:
            result.stop_reason = "max_queries"
            break

        allow_spark = spark_left > 0
        claimed = _claim_any(hunt, research, engagement)
        if claimed is None:
            result.stop_reason = "queue_empty"
            break

        queue_name, query_id, brand, query, origin, apply_keep, apply_toss = claimed
        record_heartbeat(
            ACTOR,
            status=AgentStatus.WORKING,
            task=f"{queue_name}: {query[:80]}",
        )
        try:
            decision = assess_search_query(
                brand=brand,
                query=query,
                origin=origin,
                allow_spark=allow_spark,
            )
        except Exception as exc:  # noqa: BLE001
            from agent_crm.agency.orchestrator import note_worker_failure

            note_worker_failure(
                source_agent=ImprovementSourceAgent.QUEUE_REVIEW,
                error_text=str(exc),
                context=f"{queue_name} query {query_id}",
            )
            result.errors.append(str(exc))
            apply_toss(query_id, f"review error: {exc}"[:500])
            result.reviewed += 1
            result.tossed += 1
            continue

        if decision.spark_used:
            spark_left = max(0, spark_left - 1)
            result.spark_used += 1
        if decision.keep:
            apply_keep(query_id)
            result.kept += 1
        else:
            apply_toss(query_id, decision.reason)
            result.tossed += 1
        result.reviewed += 1

    record_heartbeat(
        ACTOR,
        status=AgentStatus.IDLE,
        task=(
            f"review stopped ({result.stop_reason}): "
            f"{result.kept} kept, {result.tossed} tossed"
        ),
    )
    return result


def run_queue_review_watch(*, budget: QueueReviewBudget | None = None) -> None:
    """Drain pending_review forever, sleeping when the backlog is empty."""
    settings = get_settings()
    poll = max(5, settings.queue_review_poll_seconds)
    while True:
        wait_while_disabled(ACTOR)
        result = run_queue_review(budget=budget)
        if result.reviewed == 0:
            record_heartbeat(
                ACTOR,
                status=AgentStatus.IDLE,
                task="search queues clean; waiting for hunter-added terms",
            )
            time.sleep(poll)
            continue
        time.sleep(1.0)


def _claim_any(
    hunt: HuntStore,
    research: ResearchQueryStore,
    engagement: EngagementQueryStore,
) -> tuple | None:
    hunt_row = hunt.claim_next_pending_review_query()
    if hunt_row is not None:
        query_id, brand, query, origin = hunt_row
        return (
            "hunt",
            query_id,
            brand,
            query,
            origin,
            hunt.mark_query_kept,
            hunt.mark_query_rejected,
        )
    research_row = research.claim_next_pending_review_query()
    if research_row is not None:
        query_id, brand, query, origin = research_row
        return (
            "research",
            query_id,
            brand,
            query,
            origin,
            research.mark_query_kept,
            research.mark_query_rejected,
        )
    engagement_row = engagement.claim_next_pending_review_query()
    if engagement_row is not None:
        query_id, brand, query, origin = engagement_row
        return (
            "engagement",
            query_id,
            brand,
            query,
            origin,
            engagement.mark_query_kept,
            engagement.mark_query_rejected,
        )
    return None
