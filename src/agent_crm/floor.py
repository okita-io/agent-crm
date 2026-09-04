"""Live Agents floor helpers: queue lanes for the Vite dashboard."""

from __future__ import annotations

from agent_crm.engagement.query_store import EngagementQueryStore
from agent_crm.enums import HuntQueryStatus
from agent_crm.hunt.store import HuntStore
from agent_crm.jobs.dispatcher import build_job_status
from agent_crm.research.query_store import ResearchQueryStore
from agent_crm.seo.query_store import SeoQueryStore


def _status_count(status: dict, key: str) -> int:
    nested = status.get("by_status")
    if isinstance(nested, dict):
        return int(nested.get(key) or 0)
    return int(status.get(key) or 0)


def _prompts_for_hunt(limit: int = 3) -> list[str]:
    rows = HuntStore().list_queries(status=HuntQueryStatus.PENDING, limit=limit)
    return [row.query for row in reversed(rows) if row.query]


def build_queue_lanes() -> dict:
    """Counts and short prompt previews for the Live Agents task-queue rail."""
    hunt = HuntStore().queue_status()
    research = ResearchQueryStore().queue_status()
    engagement = EngagementQueryStore().queue_status()
    seo = SeoQueryStore().queue_status()
    jobs = build_job_status()

    review_pending = (
        _status_count(hunt, "pending_review")
        + int(research.get("pending_review") or 0)
        + int(engagement.get("pending_review") or 0)
    )
    lanes = [
        {
            "id": "research",
            "name": "Research topics",
            "agent_name": "research",
            "pending": int(research.get("pending") or 0),
            "prompts": [],
        },
        {
            "id": "engagement",
            "name": "Comment topics",
            "agent_name": "engagement",
            "pending": int(engagement.get("pending") or 0),
            "prompts": [],
        },
        {
            "id": "hunter",
            "name": "Hunter queries",
            "agent_name": "outbound_hunter",
            "pending": int(hunt.get("pending") or 0),
            "prompts": _prompts_for_hunt(),
        },
        {
            "id": "queue-review",
            "name": "Queue review",
            "agent_name": "queue-review",
            "pending": review_pending,
            "prompts": [],
        },
        {
            "id": "seo",
            "name": "SEO / GEO",
            "agent_name": "seo",
            "pending": int(seo.get("pending") or 0),
            "prompts": [],
        },
        {
            "id": "jobs",
            "name": "Verify / enrich",
            "agent_name": "job-dispatcher",
            "pending": int(jobs.get("pending_total") or 0),
            "prompts": [],
        },
    ]
    return {"waiting": sum(int(lane["pending"]) for lane in lanes), "lanes": lanes}
